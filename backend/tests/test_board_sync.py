"""Board sync: does it bring the layout in line, and does it still reject regressions?"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.decision import _awaiting_routing
from app.kicad import board, sexpr
from app.kicad.erc import KicadCli, diff_violations
from app.kicad.reader import read_project
from app.models import ErcReport, Violation

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


def test_unconnected_signatures_are_net_keyed(tmp_path: Path, requires_kicad: None) -> None:
    """KiCAD names an arbitrary representative track per unconnected cluster.

    Violation.signature() keys those on nets so the representative it happens to
    pick cannot make an unchanged cluster look like a different violation.
    """
    pcb = _board_of(_project(tmp_path))
    cli = KicadCli()
    runs = [cli.run_drc(pcb) for _ in range(3)]
    unconnected = [
        {v.signature() for v in r.violations if v.type.endswith("unconnected_items")} for r in runs
    ]
    assert unconnected[0], "fixture should report unconnected items to make this meaningful"
    assert unconnected[0] == unconnected[1] == unconnected[2]


def test_a_union_baseline_absorbs_kicads_own_drc_nondeterminism(
    tmp_path: Path, requires_kicad: None
) -> None:
    """The production guarantee, and the reason `run_drc_baseline` exists.

    KiCAD's DRC is NOT deterministic: on a byte-identical board it intermittently
    reports one or two fewer clearance violations (measured: 20 violations on 38 of
    40 runs, 19 once, 18 once). This test used to assert the opposite, and failed
    about one full-suite run in three.

    What must hold is not that single runs agree, but that nothing a single pass
    finds can look *new* against the baseline - otherwise the DRC gate rejects
    valid work at random.
    """
    pcb = _board_of(_project(tmp_path))
    cli = KicadCli()
    baseline = cli.run_drc_baseline(pcb, passes=3)
    for _ in range(4):
        single = cli.run_drc(pcb)
        assert single.ran
        spurious = diff_violations(baseline, single).new
        assert not spurious, f"union baseline still let a violation look new: {spurious}"


def test_run_drc_baseline_unions_its_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Union semantics, pinned without depending on hitting a real flap."""
    shared = _unconnected("GND")
    only_first = Violation(severity="error", type="clearance", description="a", items=["x"], nets=["A"])
    only_second = Violation(severity="warning", type="clearance", description="b", items=["y"], nets=["B"])
    passes = [
        ErcReport(ran=True, violations=[shared, only_first]),
        ErcReport(ran=True, violations=[shared, only_second]),
    ]

    cli = KicadCli()
    monkeypatch.setattr(cli, "run_drc", lambda board: passes.pop(0))
    merged = cli.run_drc_baseline(Path("unused.kicad_pcb"), passes=2)

    assert {v.signature() for v in merged.violations} == {
        shared.signature(),
        only_first.signature(),
        only_second.signature(),
    }
    assert (merged.errors, merged.warnings) == (2, 1)


def test_run_drc_baseline_keeps_a_single_pass_when_a_later_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pass that did not run must not silently shrink the baseline."""
    real = _unconnected("GND")
    passes = [ErcReport(ran=True, violations=[real]), ErcReport(ran=False)]
    cli = KicadCli()
    monkeypatch.setattr(cli, "run_drc", lambda board: passes.pop(0))
    merged = cli.run_drc_baseline(Path("unused.kicad_pcb"), passes=2)
    assert [v.signature() for v in merged.violations] == [real.signature()]
