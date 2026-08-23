"""Contracts for the ten-agent team."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.kicad.reader import Component, Pin, ProjectState, read_project
from app.models import ErcReport
from app.team.prompts import build_project_manager_prompt
from app.team.registry import AGENTS, get_agent
from app.team.schemas import (
    AgentTask,
    Architecture,
    ComponentSelection,
    DesignContext,
    LayoutProposal,
    ManufacturingReport,
    PMPlan,
    ProjectSpec,
    ReleaseRecord,
    RequirementsDoc,
    SchematicIntents,
    SimulationReport,
    VerificationReport,
    json_schema,
)

EXAMPLES = {
    PMPlan: {
        "project_goal": "ESP32 temperature-monitoring board",
        "workflow": [
            "requirements",
            "architecture",
            "components",
            "schematic",
            "layout",
            "simulation",
            "verification",
            "manufacturing",
            "qa_release",
        ],
        "status": "requirements_pending",
    },
    RequirementsDoc: {
        "requirements": [
            {
                "id": "PWR-001",
                "category": "power",
                "statement": "Logic rail nominal voltage",
                "value": "3.3",
                "unit": "V",
                "source": "request",
            }
        ],
        "power": {"input": "USB-C 5V", "logic_voltage": "3.3V", "maximum_current_ma": 500},
        "interfaces": [{"type": "I2C", "voltage": "3.3V", "devices": 2}],
        "mechanical": {"maximum_width_mm": 60, "maximum_height_mm": 40, "layers": 2},
        "constraints": ["Do not use BGA components", "Use commonly available parts"],
        "acceptance_tests": [
            "3.3V rail remains within tolerance",
            "Both sensors communicate over I2C",
            "KiCAD ERC and DRC contain no critical errors",
        ],
    },
    Architecture: {
        "blocks": [
            {"id": "POWER_INPUT", "type": "usb_c_power"},
            {"id": "REGULATOR", "type": "5v_to_3v3"},
            {"id": "MCU", "type": "esp32"},
            {"id": "SENSOR_1", "type": "i2c_temperature_sensor"},
        ],
        "connections": [
            {"from": "POWER_INPUT", "to": "REGULATOR", "signal": "5V"},
            {"from": "REGULATOR", "to": "MCU", "signal": "3V3"},
            {"from": "MCU", "to": "SENSOR_1", "signal": "I2C"},
        ],
    },
    ComponentSelection: {
        "components": [
            {
                "reference_group": "MCU",
                "manufacturer_part": "ESP32-S3-WROOM-1",
                "quantity": 1,
                "symbol": "RF_Module:ESP32-S3-WROOM-1",
                "footprint": "RF_Module:ESP32-S3-WROOM-1",
                "reason": "Supports Wi-Fi, I2C and 3.3V operation",
                "verified_constraints": ["logic_voltage", "interface", "package"],
            }
        ]
    },
    SchematicIntents: {
        "protocol": "I2C",
        "logic_voltage": "3.3V",
        "pullup_value": "4.7k",
        "protected_objects": [],
        "assumptions": [],
        "intents": [
            {
                "id": "A-001",
                "type": "connect_pins",
                "from": "U2.SDA",
                "to": "U1.GPIO21",
                "net_name": "I2C_SDA",
                "purpose": "sensor data",
            }
        ],
    },
    LayoutProposal: {
        "placements": [
            {
                "reference": "U1",
                "x": 29,
                "y": 19,
                "rotation": 0,
                "rationale": "center the controller",
            }
        ],
        "critical_nets": ["3V3", "GND"],
        "unrouted_nets": ["I2C_SDA", "I2C_SCL"],
    },
    SimulationReport: {
        "tests": [
            {
                "name": "3V3 regulator output",
                "expected": {"minimum_v": 3.2, "maximum_v": 3.4},
                "measured_v": 3.31,
                "status": "passed",
            },
            {
                "name": "regulator current margin",
                "required_ma": 310,
                "available_ma": 500,
                "margin_percent": 61.3,
                "status": "passed",
            },
        ]
    },
    VerificationReport: {
        "requirements_total": 18,
        "requirements_passed": 15,
        "requirements_failed": 1,
        "requirements_unverified": 2,
        "critical_findings": [
            {
                "requirement_id": "PWR-004",
                "finding": "Regulator current capacity is below calculated peak load",
                "evidence": {"required_ma": 620, "available_ma": 500},
            }
        ],
        "decision": "failed",
    },
    ManufacturingReport: {
        "manufacturer_profile": "generic_two_layer",
        "dfm_status": "warning",
        "findings": [
            {
                "type": "missing_test_point",
                "net": "3V3",
                "severity": "warning",
                "recommendation": "Add an accessible test point for board bring-up",
            }
        ],
        "fabrication_ready": False,
    },
    ReleaseRecord: {
        "release_status": "approved",
        "project_version": "0.1.0",
        "included_files": [
            "project.kicad_pro",
            "project.kicad_sch",
            "project.kicad_pcb",
            "bom.csv",
            "gerbers.zip",
            "drill_files.zip",
            "erc_report.json",
            "drc_report.json",
            "verification_report.json",
            "test_plan.md",
        ],
        "open_critical_findings": 0,
        "release_hash": "...",
    },
}


def _objects(value: object) -> list[dict]:
    if isinstance(value, dict):
        nested = [item for child in value.values() for item in _objects(child)]
        return ([value] if value.get("type") == "object" else []) + nested
    if isinstance(value, list):
        return [item for child in value for item in _objects(child)]
    return []


def test_all_agent_schemas_are_fully_inlined_and_closed() -> None:
    for agent in AGENTS:
        schema = json_schema(agent.output_model)
        serialized = str(schema)
        assert "$ref" not in serialized
        assert "$defs" not in serialized
        for node in _objects(schema):
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node.get("properties", {}))


@pytest.mark.parametrize("model,payload", EXAMPLES.items())
def test_each_output_model_round_trips_spec_example(model: type, payload: dict) -> None:
    parsed = model.model_validate(payload)
    assert parsed.model_dump(by_alias=True, exclude_none=True) == payload


def test_malformed_output_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RequirementsDoc.model_validate({"power": {"input": "USB-C 5V"}})


def test_registry_filters_prior_outputs_to_declared_reads() -> None:
    project = ProjectSpec(project_id="demo", request="test", current_stage="verification")
    task = AgentTask(
        task_id="TASK-001",
        assigned_agent="verification",
        objective="check the design",
        inputs=[],
    )
    prior = {
        "schematic_design": {"allowed": True},
        "pcb_layout": {"allowed": True},
        "simulation": {"allowed": True},
        "components": {"secret": True},
        "requirements": {"secret": True},
    }
    prompt = get_agent("verification").build_prompt(project, task, prior)
    assert '"allowed": true' in prompt
    assert "secret" not in prompt


def test_design_context_builds_and_renders_the_pin_table(tmp_path) -> None:
    state = ProjectState(
        project_dir=tmp_path,
        schematic_path=tmp_path / "demo.kicad_sch",
        components={
            "U1": Component(
                reference="U1",
                value="TMP102",
                lib_id="Sensor:TMP102",
                x=0,
                y=0,
                angle=0,
                uuid="u1",
                pins=[Pin("1", "SDA", "bidirectional", 0, 0, 0)],
            )
        },
        nets={"I2C_SDA": ["U1.1"]},
        pin_nets={"U1.1": "I2C_SDA"},
        pin_positions={"U1.1": (0, 0)},
    )
    context = DesignContext.from_project(state, ErcReport(ran=True, errors=1, warnings=2))
    project = ProjectSpec(
        project_id="demo",
        current_stage="project_manager",
        design_context=context,
    )
    prompt = build_project_manager_prompt(
        project,
        context,
        AgentTask(task_id="T-1", assigned_agent="project_manager", objective="plan"),
        {},
    )
    assert "U1 | TMP102 | Sensor:TMP102 | 1 | SDA | bidirectional" in prompt
    assert "I2C_SDA: U1.1" in prompt
    assert '"components"' not in prompt


def test_design_context_includes_board_footprint_inventory() -> None:
    directory = Path(__file__).parents[2] / "fixtures" / "projects" / "esp32_i2c_board"
    state = read_project(directory)
    context = DesignContext.from_project(
        state,
        ErcReport(ran=True),
        ErcReport(ran=True),
        directory / "esp32_i2c_board.kicad_pcb",
    )
    assert context.board is not None
    assert [(item.reference, item.x_mm, item.y_mm) for item in context.board.footprints] == [
        ("J1", 120.0, 75.0),
        ("U1", 65.0, 65.0),
        ("U2", 95.0, 55.0),
    ]
    project = ProjectSpec(project_id="board", current_stage="pcb_layout", design_context=context)
    prompt = get_agent("pcb_layout").build_prompt(
        project,
        AgentTask(task_id="layout-inventory", assigned_agent="pcb_layout", objective="place parts"),
        {},
    )
    assert "Placed footprints (only these existing references can be moved):" in prompt
    assert "J1 | 120 | 75" in prompt
    assert "MVP layout cannot add new footprints" in prompt
    assert "schematic symbol has no board footprint" in prompt


def test_duplicate_requirement_ids_are_rejected() -> None:
    payload = EXAMPLES[RequirementsDoc]
    duplicate = {**payload, "requirements": [payload["requirements"][0]] * 2}
    with pytest.raises(ValidationError, match="unique"):
        RequirementsDoc.model_validate(duplicate)


def test_team_runner_setting_resolves_without_mutating_optional_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = Settings(team_runner="stub")
    assert configured.team_runner == "stub"
    assert configured.resolved_team_runner == "stub"
    monkeypatch.setattr(settings, "team_runner", None)
    monkeypatch.setattr(settings, "devin_api_key", None)
    assert settings.resolved_team_runner == "stub"
