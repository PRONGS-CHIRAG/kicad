"""Read, write and sync `.kicad_pcb` files.

kicad-cli has no equivalent of Pcbnew's *Update PCB from Schematic* — KiCAD 10's
`pcb import` only reads foreign formats (Eagle, Altium) — so the sync is
implemented here, against the same S-expression reader/writer the schematic
edits already use.

What the sync does, in the order Pcbnew would:

1. append any nets the schematic has and the board lacks, keeping existing net
   numbers, because tracks reference nets by index;
2. rebind every pad to the net its schematic pin now carries, matching on
   reference plus pad number;
3. synthesise a footprint for any schematic symbol that has none on the board.

What it deliberately does not do is route. A synced board is unrouted by
design, exactly as it is after Pcbnew's own sync, and the caller is expected to
treat the resulting unconnected items as pending work rather than a regression.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import sexpr
from .reader import ProjectState
from .sexpr import Symbol as S

logger = logging.getLogger(__name__)

# Symbols that never get a footprint: power rails and ERC flags are schematic
# bookkeeping, not parts.
_VIRTUAL_PREFIXES = ("#PWR", "#FLG")

# Clearance kept between a synthesised footprint and anything already placed.
_PLACEMENT_CLEARANCE = 2.5
_PLACEMENT_STEP = 2.5
_OUTLINE_MARGIN = 3.0

# Fallback outline when a board carries no Edge.Cuts, so placement still lands
# somewhere sane rather than at the origin.
_DEFAULT_OUTLINE = (35.0, 35.0, 145.0, 100.0)


@dataclass
class BoardSync:
    """What a sync changed, and which nets it is responsible for creating."""

    nets_added: list[str] = field(default_factory=list)
    pads_rebound: list[str] = field(default_factory=list)
    footprints_added: list[str] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.nets_added or self.pads_rebound or self.footprints_added)

    @property
    def touched_nets(self) -> set[str]:
        """Nets this sync created or rebound a pad onto.

        Unconnected items on these are expected: the sync assigns nets but lays
        no copper. Unconnected items on any *other* net are real regressions.
        """
        nets = set(self.nets_added)
        for entry in self.pads_rebound:
            _, _, net = entry.rpartition(" -> ")
            if net and net != "<none>":
                nets.add(net)
        return nets

    def summary(self) -> list[str]:
        lines = []
        if self.nets_added:
            lines.append(f"added net(s) {', '.join(self.nets_added)} to the board")
        if self.pads_rebound:
            lines.append(f"rebound {len(self.pads_rebound)} pad(s): {', '.join(self.pads_rebound)}")
        if self.footprints_added:
            lines.append(f"placed footprint(s) {', '.join(self.footprints_added)}")
        if self.unplaced:
            lines.append(f"could not place {', '.join(self.unplaced)} — no free area inside the outline")
        return lines


def load(path: Path) -> list:
    return sexpr.loads(Path(path).read_text())


def save(doc: list, path: Path) -> None:
    Path(path).write_text(sexpr.dumps(doc) + "\n")


# ---------------------------------------------------------------- net table


def net_table(doc: list) -> dict[str, int]:
    """name -> number for every net declared on the board."""
    table: dict[str, int] = {}
    for node in sexpr.find_all(doc, "net"):
        # `(net N "NAME")` at board level; a pad's own `(net N "NAME")` is nested
        # inside the footprint and is not reached by this top-level scan.
        if len(node) >= 3:
            table[str(node[2])] = int(str(node[1]))
    return table


def ensure_nets(doc: list, names: list[str]) -> tuple[dict[str, int], list[str]]:
    """Append missing nets, preserving the numbers tracks already reference."""
    table = net_table(doc)
    added: list[str] = []
    if not names:
        return table, added

    next_number = max(table.values(), default=0) + 1
    last_index = max(
        (index for index, node in enumerate(doc) if isinstance(node, list) and node and node[0] == S("net")),
        default=len(doc) - 1,
    )
    for name in names:
        if not name or name in table:
            continue
        node = [S("net"), S(str(next_number)), name]
        last_index += 1
        doc.insert(last_index, node)
        table[name] = next_number
        added.append(name)
        next_number += 1
    return table, added


# ---------------------------------------------------------------- footprints


def footprint_reference(footprint: list) -> str | None:
    for text in sexpr.find_all(footprint, "fp_text"):
        if len(text) >= 3 and text[1] == S("reference"):
            return str(text[2])
    return None


def _pad_number(pad: list) -> str:
    return str(pad[1]) if len(pad) > 1 else ""


def _set_pad_net(pad: list, number: int | None, name: str | None) -> None:
    """Point a pad at a net, or clear it when the schematic pin has none."""
    for index, node in enumerate(pad):
        if isinstance(node, list) and node and node[0] == S("net"):
            if number is None:
                pad.pop(index)
            else:
                pad[index] = [S("net"), S(str(number)), name or ""]
            return
    if number is not None:
        pad.append([S("net"), S(str(number)), name or ""])


def _footprint_extent(footprint: list) -> tuple[float, float, float, float] | None:
    """Bounding box of a placed footprint's pads and graphic geometry."""
    at = sexpr.find(footprint, "at")
    if at is None or len(at) < 3:
        return None
    try:
        ox, oy = float(str(at[1])), float(str(at[2]))
    except ValueError:
        return None
    try:
        rotation = float(str(at[3])) if len(at) > 3 else 0.0
    except ValueError:
        rotation = 0.0
    layer = sexpr.find(footprint, "layer")
    mirrored = layer is not None and len(layer) > 1 and str(layer[1]) == "B.Cu"

    def rotate(point: tuple[float, float], angle: float) -> tuple[float, float]:
        radians = math.radians(angle)
        x, y = point
        return (x * math.cos(radians) - y * math.sin(radians), x * math.sin(radians) + y * math.cos(radians))

    def absolute(point: tuple[float, float]) -> tuple[float, float]:
        local = (-point[0], point[1]) if mirrored else point
        x, y = rotate(local, rotation)
        return (ox + x, oy + y)

    points: list[tuple[float, float]] = []

    def number_pair(node: list | None) -> tuple[float, float] | None:
        if node is None or len(node) < 3:
            return None
        try:
            return float(str(node[1])), float(str(node[2]))
        except (TypeError, ValueError):
            return None

    for pad in sexpr.find_all(footprint, "pad"):
        center = number_pair(sexpr.find(pad, "at"))
        size = number_pair(sexpr.find(pad, "size"))
        if center is None or size is None:
            continue
        pad_at = sexpr.find(pad, "at")
        try:
            pad_rotation = float(str(pad_at[3])) if pad_at is not None and len(pad_at) > 3 else 0.0
        except ValueError:
            pad_rotation = 0.0
        half_x, half_y = size[0] / 2, size[1] / 2
        for corner in (
            (center[0] - half_x, center[1] - half_y),
            (center[0] - half_x, center[1] + half_y),
            (center[0] + half_x, center[1] - half_y),
            (center[0] + half_x, center[1] + half_y),
        ):
            points.append(absolute(rotate(corner, pad_rotation)))

    for kind in ("fp_rect", "fp_line", "fp_arc", "fp_circle"):
        for graphic in sexpr.find_all(footprint, kind):
            for key in ("start", "mid", "end", "center"):
                point = number_pair(sexpr.find(graphic, key))
                if point is not None:
                    points.append(absolute(point))
            if kind == "fp_circle":
                center = number_pair(sexpr.find(graphic, "center"))
                end = number_pair(sexpr.find(graphic, "end"))
                if center is not None and end is not None:
                    radius = ((end[0] - center[0]) ** 2 + (end[1] - center[1]) ** 2) ** 0.5
                    for edge in (
                        (center[0] - radius, center[1]),
                        (center[0] + radius, center[1]),
                        (center[0], center[1] - radius),
                        (center[0], center[1] + radius),
                    ):
                        points.append(absolute(edge))
    for polygon in sexpr.find_all(footprint, "fp_poly"):
        pts = sexpr.find(polygon, "pts")
        if pts is not None:
            for point in sexpr.find_all(pts, "xy"):
                parsed = number_pair(point)
                if parsed is not None:
                    points.append(absolute(parsed))

    if not points:
        return None
    xs, ys = zip(*points, strict=True)
    return (min(xs), min(ys), max(xs), max(ys))


def footprint_extent(footprint: list) -> tuple[float, float, float, float] | None:
    return _footprint_extent(footprint)


def board_outline(doc: list) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for line in sexpr.find_all(doc, "gr_line"):
        if sexpr.find(line, "layer") is None:
            continue
        layer = sexpr.find(line, "layer")
        if layer is None or len(layer) < 2 or str(layer[1]) != "Edge.Cuts":
            continue
        for key in ("start", "end"):
            point = sexpr.find(line, key)
            if point is not None and len(point) >= 3:
                try:
                    xs.append(float(str(point[1])))
                    ys.append(float(str(point[2])))
                except ValueError:
                    continue
    if not xs or not ys:
        return _DEFAULT_OUTLINE
    return (min(xs), min(ys), max(xs), max(ys))


def free_position(
    doc: list, half_w: float, half_h: float, reserved: list[tuple]
) -> tuple[float, float] | None:
    """First grid slot inside the outline that clears every placed footprint."""
    min_x, min_y, max_x, max_y = board_outline(doc)
    occupied = [e for e in (_footprint_extent(fp) for fp in sexpr.find_all(doc, "footprint")) if e]
    occupied.extend(reserved)

    y = min_y + _OUTLINE_MARGIN + half_h
    while y + half_h + _OUTLINE_MARGIN <= max_y:
        x = min_x + _OUTLINE_MARGIN + half_w
        while x + half_w + _OUTLINE_MARGIN <= max_x:
            box = (x - half_w, y - half_h, x + half_w, y + half_h)
            clash = any(
                box[0] - _PLACEMENT_CLEARANCE < ox2
                and box[2] + _PLACEMENT_CLEARANCE > ox1
                and box[1] - _PLACEMENT_CLEARANCE < oy2
                and box[3] + _PLACEMENT_CLEARANCE > oy1
                for ox1, oy1, ox2, oy2 in occupied
            )
            if not clash:
                return (round(x, 2), round(y, 2))
            x += _PLACEMENT_STEP
        y += _PLACEMENT_STEP
    return None


def _num(value: float) -> S:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return S(text or "0")


def resistor_footprint(reference: str, value: str, x: float, y: float) -> list:
    """A 0805-style two-pad SMD resistor, in the same idiom as the fixtures."""
    half_w, half_h = 1.6, 0.9
    return [
        S("footprint"),
        "Mitos:R_0805",
        [S("layer"), "F.Cu"],
        [S("at"), _num(x), _num(y)],
        [S("attr"), S("smd")],
        [
            S("fp_text"),
            S("reference"),
            reference,
            [S("at"), S("0"), _num(-half_h - 1.2)],
            [S("layer"), "F.SilkS"],
            [S("effects"), [S("font"), [S("size"), S("1"), S("1")], [S("thickness"), S("0.15")]]],
        ],
        [
            S("fp_text"),
            S("value"),
            value,
            [S("at"), S("0"), _num(half_h + 1.2)],
            [S("layer"), "F.Fab"],
            [S("effects"), [S("font"), [S("size"), S("0.8"), S("0.8")], [S("thickness"), S("0.12")]]],
        ],
        [
            S("fp_rect"),
            [S("start"), _num(-half_w), _num(-half_h)],
            [S("end"), _num(half_w), _num(half_h)],
            [S("stroke"), [S("width"), S("0.2")], [S("type"), S("default")]],
            [S("fill"), S("none")],
            [S("layer"), "F.SilkS"],
        ],
        _pad("1", -0.95, 0.0),
        _pad("2", 0.95, 0.0),
    ]


def _pad(number: str, x: float, y: float) -> list:
    return [
        S("pad"),
        number,
        S("smd"),
        S("roundrect"),
        [S("at"), _num(x), _num(y)],
        [S("size"), S("1.0"), S("1.45")],
        [S("layers"), "F.Cu", "F.Paste", "F.Mask"],
        [S("roundrect_rratio"), S("0.2")],
    ]


# ---------------------------------------------------------------- the sync


def sync_to_schematic(board_path: Path, state: ProjectState) -> BoardSync:
    """Bring `board_path` in line with `state`, the schematic as just written."""
    board_path = Path(board_path)
    doc = load(board_path)
    result = BoardSync()

    placeable = {
        reference: component
        for reference, component in state.components.items()
        if not component.is_power and not reference.startswith(_VIRTUAL_PREFIXES)
    }

    wanted_nets = sorted({net for net in state.pin_nets.values() if net})
    table, result.nets_added = ensure_nets(doc, wanted_nets)

    # 1. Rebind pads on footprints that are already placed.
    placed: dict[str, list] = {}
    for footprint in sexpr.find_all(doc, "footprint"):
        reference = footprint_reference(footprint)
        if reference:
            placed[reference] = footprint

    for reference, footprint in placed.items():
        if reference not in placeable:
            continue
        for pad in sexpr.find_all(footprint, "pad"):
            number = _pad_number(pad)
            wanted = state.pin_nets.get(f"{reference}.{number}")
            current = sexpr.find(pad, "net")
            current_name = str(current[2]) if current is not None and len(current) >= 3 else None
            if (wanted or None) == current_name:
                continue
            _set_pad_net(pad, table.get(wanted) if wanted else None, wanted)
            result.pads_rebound.append(f"{reference}.{number} -> {wanted or '<none>'}")

    # 2. Place footprints for symbols the board has never seen.
    reserved: list[tuple] = []
    for reference in sorted(reference for reference in placeable if reference not in placed):
        component = placeable[reference]
        if not reference.startswith("R"):
            # Only passives Mitos itself adds are synthesised; anything else needs
            # a real footprint choice that belongs to the person, not the tool.
            result.unplaced.append(reference)
            continue
        spot = free_position(doc, 1.6, 0.9, reserved)
        if spot is None:
            result.unplaced.append(reference)
            continue
        x, y = spot
        footprint = resistor_footprint(reference, component.value, x, y)
        for pad in sexpr.find_all(footprint, "pad"):
            wanted = state.pin_nets.get(f"{reference}.{_pad_number(pad)}")
            if wanted:
                _set_pad_net(pad, table.get(wanted), wanted)
        doc.append(footprint)
        reserved.append((x - 1.6, y - 0.9, x + 1.6, y + 0.9))
        result.footprints_added.append(reference)

    if result.changed:
        save(doc, board_path)
        logger.info("synced board %s: %s", board_path.name, "; ".join(result.summary()))
    return result
