"""Board sync: does it bring the layout in line, and does it still reject regressions?"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.decision import _awaiting_routing
from app.kicad import board, sexpr
from app.kicad.erc import KicadCli, diff_violations
from app.kicad.reader import read_project
from app.models import Violation

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "projects"
WITH_BOARD = "esp32_i2c_board"


def _project(tmp_path: Path, name: str = WITH_BOARD) -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURES / name, target)
    return target


def _board_of(project: Path) -> Path:
    return next(project.glob("*.kicad_pcb"))


# --------------------------------------------------------------- exemption


def _unconnected(*nets: str) -> Violation:
    return Violation(
        severity="error",
        type="unconnected_items.unconnected_items",
        description="Missing connection between items",
        items=[f"Pad 1 [{net}] of U1 on F.Cu" for net in nets],
        nets=sorted(nets),
    )


def test_unconnected_on_a_synced_net_is_pending_routing() -> None:
    assert _awaiting_routing(_unconnected("I2C_SDA"), {"I2C_SDA", "GND"})


def test_unconnected_on_an_untouched_net_still_rejects() -> None:
    """Severing existing copper must never be waved through as 'pending routing'."""
    assert not _awaiting_routing(_unconnected("VBUS"), {"I2C_SDA"})
    # Mixed: one net was synced, the other was not. Still a regression.
    assert not _awaiting_routing(_unconnected("I2C_SDA", "VBUS"), {"I2C_SDA"})


def test_only_unconnected_items_are_ever_exempt() -> None:
    clearance = Violation(
        severity="error",
        type="clearance",
        description="Clearance violation",
        items=["Track [I2C_SDA] on F.Cu"],
        nets=["I2C_SDA"],
    )
    assert not _awaiting_routing(clearance, {"I2C_SDA"})


def test_a_violation_naming_no_net_is_never_exempt() -> None:
    assert not _awaiting_routing(_unconnected(), {"I2C_SDA"})


# --------------------------------------------------------------- net table


def test_ensure_nets_preserves_existing_numbers(tmp_path: Path) -> None:
    """Tracks reference nets by index, so renumbering would silently rewire the board."""
    pcb = _board_of(_project(tmp_path))
    doc = board.load(pcb)
    before = board.net_table(doc)

    table, added = board.ensure_nets(doc, ["GND", "I2C_SDA", "I2C_SCL"])

    assert added == ["I2C_SDA", "I2C_SCL"]
    for name, number in before.items():
        assert table[name] == number
    assert table["I2C_SDA"] not in before.values()
    assert table["I2C_SCL"] != table["I2C_SDA"]


def test_sync_is_a_noop_when_the_board_already_matches(tmp_path: Path) -> None:
    project = _project(tmp_path)
    pcb = _board_of(project)
    original = pcb.read_bytes()

    result = board.sync_to_schematic(pcb, read_project(project))

    assert not result.changed
    assert pcb.read_bytes() == original, "an in-sync board must not be rewritten"


# --------------------------------------------------------------- full sync


@pytest.fixture
def synced(tmp_path: Path, requires_kicad: None) -> tuple[Path, board.BoardSync]:
    """A project whose schematic gained I2C nets and pull-ups, then got synced."""
    from app.execution.local import LocalExecutor
    from app.planning.generator import generate_plan

    project = _project(tmp_path)
    state = read_project(project)
    plan = generate_plan(state, ["U1", "U2"], "Connect over I2C at 3.3V and add pull-ups.")
    LocalExecutor().execute(project, plan)
    after = read_project(project)
    return project, board.sync_to_schematic(_board_of(project), after)


def test_sync_adds_nets_rebinds_pads_and_places_footprints(
    synced: tuple[Path, board.BoardSync],
) -> None:
    _, result = synced
    assert sorted(result.nets_added) == ["I2C_SCL", "I2C_SDA"]
    assert sorted(result.footprints_added) == ["R1", "R2"]
    assert not result.unplaced
    rebound = {entry.split(" -> ")[0] for entry in result.pads_rebound}
    assert {"U1.3", "U1.4", "U2.3", "U2.4"} <= rebound


def test_synced_board_still_parses_and_loses_parity_warnings(
    synced: tuple[Path, board.BoardSync], tmp_path: Path
) -> None:
    project, _ = synced
    cli = KicadCli()
    report = cli.run_drc(_board_of(project))
    assert report.ran, "kicad-cli must still be able to read the board we wrote"

    # Pins the schematic leaves unconnected keep a net conflict of their own:
    # KiCAD invents `unconnected-(U1-GPIO25-Pad5)` for them, which is not a net
    # the schematic actually carries. What must be gone is every conflict on a
    # pin that does have a real net.
    state = read_project(project)
    real_nets = {net for net in state.pin_nets.values() if net}
    conflicts = [
        v
        for v in report.violations
        if v.type.endswith("net_conflict") and "unconnected-(" not in v.description
    ]
    assert not conflicts, f"pads still missing a net the schematic assigns: {conflicts}"
    assert {"I2C_SDA", "I2C_SCL"} <= real_nets


def test_placed_footprints_sit_inside_the_board_outline(
    synced: tuple[Path, board.BoardSync],
) -> None:
    project, _ = synced
    doc = board.load(_board_of(project))
    min_x, min_y, max_x, max_y = board.board_outline(doc)

    placed = 0
    for footprint in sexpr.find_all(doc, "footprint"):
        if board.footprint_reference(footprint) not in ("R1", "R2"):
            continue
        placed += 1
        extent = board._footprint_extent(footprint)
        assert extent is not None
        x1, y1, x2, y2 = extent
        assert min_x <= x1 and x2 <= max_x, "footprint escaped the outline horizontally"
        assert min_y <= y1 and y2 <= max_y, "footprint escaped the outline vertically"
    assert placed == 2


def test_placed_footprints_do_not_overlap_anything(
    synced: tuple[Path, board.BoardSync],
) -> None:
    doc = board.load(_board_of(synced[0]))
    extents = [
        (board.footprint_reference(fp), board._footprint_extent(fp))
        for fp in sexpr.find_all(doc, "footprint")
    ]
    boxes = [(ref, e) for ref, e in extents if e]
    for i, (ref_a, a) in enumerate(boxes):
        for ref_b, b in boxes[i + 1 :]:
            overlaps = a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
            assert not overlaps, f"{ref_a} overlaps {ref_b}"


def test_sync_is_idempotent(synced: tuple[Path, board.BoardSync]) -> None:
    """Running twice must not keep rewriting the board or duplicate footprints."""
    project, _ = synced
    pcb = _board_of(project)
    settled = pcb.read_bytes()

    again = board.sync_to_schematic(pcb, read_project(project))

    assert not again.changed
    assert pcb.read_bytes() == settled


def test_new_unconnected_items_are_all_attributable_to_synced_nets(
    synced: tuple[Path, board.BoardSync], tmp_path: Path
) -> None:
    """The exemption is only sound if every new unconnected item names a synced net."""
    project, result = synced
    cli = KicadCli()
    pristine = _project(tmp_path / "pristine")
    before = cli.run_drc(_board_of(pristine))
    after = cli.run_drc(_board_of(project))

    new_errors = [v for v in diff_violations(before, after).new if v.severity == "error"]
    unexplained = [v for v in new_errors if not _awaiting_routing(v, result.touched_nets)]
    assert not unexplained, f"new DRC errors the sync cannot account for: {unexplained}"


# --------------------------------------------------------------- placement


def test_footprint_on_net_finds_the_synthesised_resistor(
    synced: tuple[Path, board.BoardSync],
) -> None:
    project, result = synced
    doc = board.load(_board_of(project))
    reference = board.footprint_on_net(doc, "I2C_SDA", result.footprints_added)
    assert reference in result.footprints_added


def test_footprint_on_net_returns_none_for_no_match(
    synced: tuple[Path, board.BoardSync],
) -> None:
    project, result = synced
    doc = board.load(_board_of(project))
    assert board.footprint_on_net(doc, "NO_SUCH_NET", result.footprints_added) is None


def test_move_footprint_near_gets_strictly_closer_to_the_target(
    synced: tuple[Path, board.BoardSync],
) -> None:
    """The point of `near`: land closer to the target than the default grid scan did."""
    project, result = synced
    doc = board.load(_board_of(project))
    reference = board.footprint_on_net(doc, "I2C_SDA", result.footprints_added)
    assert reference is not None

    def _center(extent: tuple[float, float, float, float]) -> tuple[float, float]:
        return ((extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2)

    def _distance(a: tuple, b: tuple) -> float:
        (ax, ay), (bx, by) = _center(a), _center(b)
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

    target_extent = board._footprint_extent(board.footprint_by_reference(doc, "U1"))
    before_extent = board._footprint_extent(board.footprint_by_reference(doc, reference))
    assert target_extent is not None and before_extent is not None

    assert board.move_footprint_near(doc, reference, "U1") is True
    after_extent = board._footprint_extent(board.footprint_by_reference(doc, reference))
    assert after_extent is not None

    assert _distance(after_extent, target_extent) < _distance(before_extent, target_extent)

    min_x, min_y, max_x, max_y = board.board_outline(doc)
    x1, y1, x2, y2 = after_extent
    assert min_x <= x1 and x2 <= max_x
    assert min_y <= y1 and y2 <= max_y

    for footprint in sexpr.find_all(doc, "footprint"):
        if board.footprint_reference(footprint) == reference:
            continue
        other = board._footprint_extent(footprint)
        if other is None:
            continue
        ox1, oy1, ox2, oy2 = other
        assert not (x1 < ox2 and x2 > ox1 and y1 < oy2 and y2 > oy1), (
            f"moved {reference} overlaps {board.footprint_reference(footprint)}"
        )


def test_move_footprint_near_is_a_no_op_for_unknown_references(
    synced: tuple[Path, board.BoardSync],
) -> None:
    project, result = synced
    doc = board.load(_board_of(project))
    reference = result.footprints_added[0]
    assert board.move_footprint_near(doc, "R404", "U1") is False
    assert board.move_footprint_near(doc, reference, "U404") is False


def test_drc_diffing_is_deterministic(tmp_path: Path, requires_kicad: None) -> None:
    """KiCAD names an arbitrary representative track per unconnected cluster.

    Violation.signature() keys those on nets for exactly this reason; without it a
    DRC gate would reject valid changes at random.
    """
    pcb = _board_of(_project(tmp_path))
    cli = KicadCli()
    reports = [cli.run_drc(pcb) for _ in range(4)]
    for earlier, later in zip(reports, reports[1:], strict=False):
        diff = diff_violations(earlier, later)
        assert not diff.new and not diff.resolved, "DRC diff flapped on an unchanged board"
