from __future__ import annotations

import json
from pathlib import Path

from app.kicad import sexpr, writer
from app.kicad.checkpoint import Checkpoint
from app.kicad.erc import KicadCli, diff_violations, parse_report
from app.kicad.reader import read_project
from app.kicad.state import diff_states, touches_protected

ERC_SAMPLE = {
    "kicad_version": "8.0.9",
    "sheets": [
        {
            "path": "/",
            "violations": [
                {
                    "type": "pin_not_connected",
                    "severity": "error",
                    "description": "Pin not connected",
                    "items": [{"description": "Symbol U2 Pin 3 [SDA]"}],
                },
                {
                    "type": "lib_symbol_issues",
                    "severity": "warning",
                    "description": "Library not found",
                    "items": [{"description": "Symbol U1"}],
                },
            ],
        }
    ],
}


def test_sexpr_round_trip(project: Path) -> None:
    schematic = next(project.glob("*.kicad_sch"))
    original = sexpr.loads(schematic.read_text())
    reparsed = sexpr.loads(sexpr.dumps(original))
    assert reparsed == original


def test_reader_extracts_components_and_nets(state) -> None:
    assert set(state.components) >= {"U1", "U2", "J1"}
    assert state.pin_nets["U1.1"] == "+3V3"
    assert sorted(state.nets["USB_D+"]) == ["J1.3", "U1.6"]
    assert "U2.3" not in state.pin_nets


def test_pin_lookup_by_name_and_number(state) -> None:
    assert state.components["U2"].pin("SDA").number == "3"
    assert state.components["U2"].pin("3").name == "SDA"


def test_parse_and_diff_erc_reports() -> None:
    before = parse_report(ERC_SAMPLE)
    assert (before.errors, before.warnings) == (1, 1)

    after_data = json.loads(json.dumps(ERC_SAMPLE))
    after_data["sheets"][0]["violations"][0]["items"] = [{"description": "Symbol U2 Pin 4 [SCL]"}]
    after = parse_report(after_data)

    diff = diff_violations(before, after)
    assert len(diff.new) == 1 and len(diff.resolved) == 1 and len(diff.unchanged) == 1
    assert diff.new_critical and diff.new_critical[0].type == "pin_not_connected"


def test_erc_count_only_comparison_would_be_wrong() -> None:
    """One error resolved and a different one introduced keeps the count identical."""
    before = parse_report(ERC_SAMPLE)
    after_data = json.loads(json.dumps(ERC_SAMPLE))
    after_data["sheets"][0]["violations"][0]["items"] = [{"description": "Symbol U9 Pin 1 [VCC]"}]
    after = parse_report(after_data)
    assert before.errors == after.errors
    assert diff_violations(before, after).new_critical


def test_checkpoint_restores_after_modification(project: Path, tmp_path: Path) -> None:
    checkpoint = Checkpoint.create(project, tmp_path / "cp")
    schematic = next(project.glob("*.kicad_sch"))
    doc = writer.load(schematic)
    writer.add_global_label(doc, "I2C_SDA", 10, 10)
    writer.save(doc, schematic)

    assert checkpoint.files_changed() == [schematic.name]
    assert checkpoint.restore() is True
    assert checkpoint.files_changed() == []


def test_checkpoint_removes_files_added_after_the_checkpoint(project: Path, tmp_path: Path) -> None:
    checkpoint = Checkpoint.create(project, tmp_path / "cp")
    (project / "extra.kicad_sch").write_text("(kicad_sch)")
    assert checkpoint.restore() is True
    assert not (project / "extra.kicad_sch").exists()


def test_state_diff_and_protected_detection(project: Path) -> None:
    before = read_project(project)
    schematic = before.schematic_path
    doc = writer.load(schematic)
    position = before.pin_position("U2", "SDA")
    writer.add_global_label(doc, "USB_D+", *position)
    writer.save(doc, schematic)

    diff = diff_states(before, read_project(project))
    assert diff.added_pin_nets == {"U2.3": "USB_D+"}
    assert touches_protected(diff, ["J1", "USB_D+", "USB_D-"]) == ["pin U2.3"]


def test_cli_capability_discovery() -> None:
    cli = KicadCli()
    if not cli.available:
        return
    assert cli.version()
    assert cli.supports("sch", "erc")
    assert cli.supports("pcb", "drc")
    assert not cli.supports("sch", "nonexistent")
