"""Schematic intent application coverage against a real fixture."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.kicad.erc import KicadCli, diff_violations
from app.kicad.reader import read_project
from app.team.schemas import SchematicIntents
from app.team.schematic import apply_schematic_intents

FIXTURE = Path(__file__).parents[2] / "fixtures" / "projects" / "esp32_i2c_demo"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    return project


def _live_intents() -> SchematicIntents:
    fixture = Path(__file__).parent / "fixtures" / "live_schematic_design_tenth.json"
    return SchematicIntents.model_validate(json.loads(fixture.read_text()))


def test_power_symbol_intents_are_rejected_before_any_write(tmp_path: Path) -> None:
    project = _project(tmp_path)
    schematic = next(project.glob("*.kicad_sch"))
    original = schematic.read_bytes()

    with pytest.raises(ValueError, match="power symbols and power flags"):
        apply_schematic_intents(_live_intents(), project)

    assert schematic.read_bytes() == original


def test_live_tenth_intents_renet_en_without_new_erc_errors(tmp_path: Path, requires_kicad: None) -> None:
    project = _project(tmp_path)
    intents = _live_intents()
    intents = intents.model_copy(
        update={
            "intents": [
                intent
                for intent in intents.intents
                if intent.id not in {"SCH-011", "SCH-012", "SCH-013", "SCH-014"}
            ]
        }
    )
    schematic = next(project.glob("*.kicad_sch"))
    cli = KicadCli()
    baseline = cli.run_erc(schematic)
    assert baseline.ran

    apply_schematic_intents(intents, project)

    after_state = read_project(project)
    assert after_state.pin_nets["U1.8"] == "EN"
    assert "+3V3" not in {net for pin, net in after_state.pin_nets.items() if pin == "U1.8"}
    after = cli.run_erc(schematic)
    assert after.ran
    diff = diff_violations(baseline, after)
    assert not diff.new_critical


def test_invalid_pin_is_rejected_before_any_write(tmp_path: Path) -> None:
    project = _project(tmp_path)
    schematic = next(project.glob("*.kicad_sch"))
    original = schematic.read_bytes()
    intents = SchematicIntents(
        protocol="I2C",
        logic_voltage="3.3V",
        pullup_value="",
        protected_objects=[],
        assumptions=[],
        intents=[
            {
                "id": "bad",
                "type": "connect_pin_to_net",
                "pin": "U99.SDA",
                "net": "I2C_SDA",
            },
        ],
    )
    with pytest.raises(ValueError, match="not found"):
        apply_schematic_intents(intents, project)
    assert schematic.read_bytes() == original


def test_all_supported_intents_are_written_through_shared_editors(tmp_path: Path) -> None:
    project = _project(tmp_path)
    intents = SchematicIntents(
        protocol="I2C",
        logic_voltage="3.3V",
        pullup_value="",
        protected_objects=[],
        assumptions=[],
        intents=[
            {
                "id": "connect",
                "type": "connect_pins",
                "from": "U1.GPIO21",
                "to": "U2.SDA",
                "net_name": "I2C_SDA",
            },
            {
                "id": "label",
                "type": "connect_pin_to_net",
                "pin": "U2.SCL",
                "net": "I2C_SCL",
            },
            {
                "id": "pullup",
                "type": "ensure_pullup",
                "net": "I2C_SDA",
                "to_net": "+3V3",
                "value": "4.7k",
            },
        ],
    )
    before = read_project(project)
    apply_schematic_intents(intents, project)
    after = read_project(project)
    assert after.pin_nets.get("U2.3") == "I2C_SDA"
    assert after.pin_nets.get("U2.4") == "I2C_SCL"
    assert before.pin_nets.get("U2.3") != after.pin_nets.get("U2.3")
    assert any(component.value == "4.7k" for component in after.components.values())
