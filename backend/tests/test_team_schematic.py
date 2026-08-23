"""Schematic intent application coverage against a real fixture."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.kicad.reader import read_project
from app.team.schemas import SchematicIntents
from app.team.schematic import apply_schematic_intents

FIXTURE = Path(__file__).parents[2] / "fixtures" / "projects" / "esp32_i2c_demo"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    return project


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
