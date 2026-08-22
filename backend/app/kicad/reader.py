"""Read a KiCAD schematic into a structured, comparable project state."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from . import sexpr
from .sexpr import Symbol

POWER_LIB_PREFIX = "power:"
LABEL_NODES = ("label", "global_label", "hierarchical_label")


@dataclass(frozen=True)
class Pin:
    number: str
    name: str
    electrical_type: str
    x: float
    y: float
    angle: float


@dataclass
class Component:
    reference: str
    value: str
    lib_id: str
    x: float
    y: float
    angle: float
    uuid: str
    pins: list[Pin] = field(default_factory=list)
    is_power: bool = False

    def pin(self, key: str) -> Pin | None:
        wanted = key.strip().upper()
        for pin in self.pins:
            if pin.name.upper() == wanted or pin.number.upper() == wanted:
                return pin
        return None


@dataclass
class ProjectState:
    """Normalized schematic state used for planning, diffing and validation."""

    project_dir: Path
    schematic_path: Path
    components: dict[str, Component]
    nets: dict[str, list[str]]
    pin_nets: dict[str, str]
    pin_positions: dict[str, tuple[float, float]]

    def pin_position(self, reference: str, pin_key: str) -> tuple[float, float] | None:
        component = self.components.get(reference)
        if component is None:
            return None
        pin = component.pin(pin_key)
        if pin is None:
            return None
        return self.pin_positions.get(f"{reference}.{pin.number}")

    def component_summaries(self) -> list[dict]:
        return [
            {
                "reference": comp.reference,
                "value": comp.value,
                "lib_id": comp.lib_id,
                "is_power": comp.is_power,
                "pins": [
                    {
                        "number": pin.number,
                        "name": pin.name,
                        "type": pin.electrical_type,
                        "net": self.pin_nets.get(f"{comp.reference}.{pin.number}"),
                    }
                    for pin in comp.pins
                ],
            }
            for comp in sorted(self.components.values(), key=lambda c: c.reference)
            if not comp.is_power
        ]

    def to_dict(self) -> dict:
        return {
            "components": {
                ref: {"value": c.value, "lib_id": c.lib_id, "is_power": c.is_power}
                for ref, c in sorted(self.components.items())
            },
            "pin_nets": dict(sorted(self.pin_nets.items())),
            "nets": {name: sorted(pins) for name, pins in sorted(self.nets.items())},
        }


def _key(x: float, y: float) -> tuple[float, float]:
    return (round(x, 3), round(y, 3))


def _transform(
    sx: float, sy: float, angle: float, mirror: str | None, px: float, py: float
) -> tuple[float, float]:
    """Absolute sheet position of a library pin placed on a symbol instance."""
    x, y = px, -py
    if mirror == "y":
        x = -x
    elif mirror == "x":
        y = -y
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    rx = x * cos_a + y * sin_a
    ry = -x * sin_a + y * cos_a
    return sx + rx, sy + ry


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[float, float], tuple[float, float]] = {}

    def find(self, item: tuple[float, float]) -> tuple[float, float]:
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _lib_symbol_pins(lib_symbol: list) -> list[Pin]:
    pins: list[Pin] = []
    for unit in sexpr.find_all(lib_symbol, "symbol"):
        for pin_node in sexpr.find_all(unit, "pin"):
            at = sexpr.find(pin_node, "at")
            name_node = sexpr.find(pin_node, "name")
            number_node = sexpr.find(pin_node, "number")
            if at is None or name_node is None or number_node is None:
                continue
            pins.append(
                Pin(
                    number=sexpr.atom(number_node),
                    name=sexpr.atom(name_node),
                    electrical_type=sexpr.atom(pin_node),
                    x=sexpr.number(at, 1),
                    y=sexpr.number(at, 2),
                    angle=sexpr.number(at, 3) if len(at) > 3 else 0.0,
                )
            )
    return pins


def _property(symbol_node: list, key: str) -> str | None:
    for prop in sexpr.find_all(symbol_node, "property"):
        if sexpr.atom(prop, 1) == key:
            return sexpr.atom(prop, 2)
    return None


def find_schematic(project_dir: Path) -> Path:
    schematics = sorted(project_dir.glob("*.kicad_sch"))
    if not schematics:
        raise FileNotFoundError(f"no .kicad_sch file found in {project_dir}")
    pro = sorted(project_dir.glob("*.kicad_pro"))
    if pro:
        preferred = project_dir / f"{pro[0].stem}.kicad_sch"
        if preferred.exists():
            return preferred
    return schematics[0]


def read_project(project_dir: Path) -> ProjectState:
    project_dir = Path(project_dir)
    schematic_path = find_schematic(project_dir)
    doc = sexpr.loads(schematic_path.read_text())

    lib_symbols: dict[str, list[Pin]] = {}
    lib_node = sexpr.find(doc, "lib_symbols")
    if lib_node is not None:
        for lib_symbol in sexpr.find_all(lib_node, "symbol"):
            lib_symbols[sexpr.atom(lib_symbol)] = _lib_symbol_pins(lib_symbol)

    uf = _UnionFind()
    for wire in sexpr.find_all(doc, "wire"):
        pts = sexpr.find(wire, "pts")
        if pts is None:
            continue
        points = [_key(sexpr.number(p, 1), sexpr.number(p, 2)) for p in sexpr.find_all(pts, "xy")]
        for point in points[1:]:
            uf.union(points[0], point)

    components: dict[str, Component] = {}
    pin_points: dict[str, tuple[float, float]] = {}
    label_at_point: dict[tuple[float, float], str] = {}

    for symbol_node in sexpr.find_all(doc, "symbol"):
        lib_id_node = sexpr.find(symbol_node, "lib_id")
        at = sexpr.find(symbol_node, "at")
        if lib_id_node is None or at is None:
            continue
        lib_id = sexpr.atom(lib_id_node)
        reference = _property(symbol_node, "Reference") or ""
        value = _property(symbol_node, "Value") or ""
        uuid_node = sexpr.find(symbol_node, "uuid")
        mirror_node = sexpr.find(symbol_node, "mirror")
        mirror = sexpr.atom(mirror_node) if mirror_node is not None else None
        sx, sy = sexpr.number(at, 1), sexpr.number(at, 2)
        angle = sexpr.number(at, 3) if len(at) > 3 else 0.0
        is_power = lib_id.startswith(POWER_LIB_PREFIX)
        comp = Component(
            reference=reference,
            value=value,
            lib_id=lib_id,
            x=sx,
            y=sy,
            angle=angle,
            uuid=sexpr.atom(uuid_node) if uuid_node is not None else "",
            pins=lib_symbols.get(lib_id, []),
            is_power=is_power,
        )
        if not reference:
            continue
        components[reference] = comp
        for pin in comp.pins:
            point = _key(*_transform(sx, sy, angle, mirror, pin.x, pin.y))
            pin_points[f"{reference}.{pin.number}"] = point
            if is_power:
                label_at_point.setdefault(point, value)

    for label_kind in LABEL_NODES:
        for label in sexpr.find_all(doc, label_kind):
            at = sexpr.find(label, "at")
            if at is None:
                continue
            label_at_point[_key(sexpr.number(at, 1), sexpr.number(at, 2))] = sexpr.atom(label)

    # Labels sharing a name are electrically the same net (global labels / power).
    name_roots: dict[str, tuple[float, float]] = {}
    for point, name in label_at_point.items():
        if name in name_roots:
            uf.union(name_roots[name], point)
        else:
            name_roots[name] = point

    pin_nets: dict[str, str] = {}
    nets: dict[str, list[str]] = {}
    root_names: dict[tuple[float, float], str] = {}
    for point, name in label_at_point.items():
        root_names.setdefault(uf.find(point), name)

    for pin_key, point in sorted(pin_points.items()):
        reference = pin_key.split(".")[0]
        if components[reference].is_power:
            continue
        root = uf.find(point)
        net_name = root_names.get(root)
        if net_name is None:
            connected = [k for k, p in pin_points.items() if uf.find(p) == root]
            if len(connected) < 2:
                continue
            net_name = f"Net-({sorted(connected)[0]})"
            root_names[root] = net_name
        pin_nets[pin_key] = net_name
        nets.setdefault(net_name, []).append(pin_key)

    return ProjectState(
        project_dir=project_dir,
        schematic_path=schematic_path,
        components=components,
        nets=nets,
        pin_nets=pin_nets,
        pin_positions=pin_points,
    )


def pin_net(state: ProjectState, reference: str, pin_key: str) -> str | None:
    comp = state.components.get(reference)
    if comp is None:
        return None
    pin = comp.pin(pin_key)
    if pin is None:
        return None
    return state.pin_nets.get(f"{reference}.{pin.number}")


__all__ = [
    "Component",
    "Pin",
    "ProjectState",
    "Symbol",
    "find_schematic",
    "pin_net",
    "read_project",
]
