"""Contracts for the ten-agent team."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.kicad.reader import Component, Pin, ProjectState
from app.models import ErcReport
from app.team.prompts import (
    build_project_manager_prompt,
    build_repair_prompt,
    build_requirements_prompt,
)
from app.team.registry import AGENTS, get_agent, repair_spec
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
    Requirement,
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
    """A record is closed; a free-form mapping is not, and that distinction matters.

    Closing every object indiscriminately left the mappings - `evidence`,
    `profile_rules`, `checklist`, a rail test's `expected` - as objects with no
    declared properties and `additionalProperties: false`, whose one legal value
    is `{}`. Three stages were being asked by their gates to fill fields their
    own output schema forbade filling, and no amount of prompting could fix it.
    """
    for agent in AGENTS:
        schema = json_schema(agent.output_model)
        serialized = str(schema)
        assert "$ref" not in serialized
        assert "$defs" not in serialized
        for node in _objects(schema):
            if "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            else:
                # An open map: it says what its values look like, not which keys
                # are allowed, and it carries no `required` list to satisfy.
                assert node.get("additionalProperties") is not False
                assert "required" not in node


def test_a_free_form_mapping_can_actually_hold_something() -> None:
    """The three fields whose gates require content, and one that showed it.

    A live run returned `expected: {}` on every rail test and `evidence: {}` on
    every verification finding. That looked like the agents being lazy; it was
    the only value their schema permitted.
    """
    evidence = json_schema(VerificationReport)["properties"]["critical_findings"]["items"]
    assert evidence["properties"]["evidence"]["additionalProperties"] is not False
    # The finding around it is still a closed record with every field required.
    assert evidence["additionalProperties"] is False
    assert "evidence_source" in evidence["required"]

    def open_map(field: dict) -> dict:
        return next(item for item in field.get("anyOf", [field]) if item.get("type") == "object")

    rules = open_map(json_schema(ManufacturingReport)["properties"]["profile_rules"])
    assert rules["additionalProperties"] == {"type": "number"}
    checklist = open_map(json_schema(ReleaseRecord)["properties"]["checklist"])
    assert checklist["additionalProperties"] == {"type": "boolean"}

    # And the documents the gates want still validate.
    ManufacturingReport.model_validate(
        {
            "manufacturer_profile": "generic_two_layer",
            "dfm_status": "passed",
            "findings": [],
            "fabrication_ready": True,
            "profile_rules": {"minimum_trace_width_mm": 0.15},
            "profile_provenance": "repository values",
        }
    )
    ReleaseRecord.model_validate(
        {
            "release_status": "needs_human_review",
            "project_version": "rev-1",
            "included_files": ["a.kicad_pcb"],
            "open_critical_findings": 0,
            "release_hash": "abc",
            "checklist": {"drc_passes": False},
        }
    )


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
        # Verification reads the requirements it counts, but never the plan or
        # the block diagram behind them.
        "requirements": {"allowed": True},
        "project_manager": {"secret": True},
        "architecture": {"secret": True},
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
    # A reasoning-only stage is told not to spend minutes exploring a repository
    # it does not need, and a first pass carries no rejection block.
    assert "Do not clone or read the" in prompt
    assert "REJECTED" not in prompt


def test_a_sent_back_stage_is_told_what_the_gate_rejected() -> None:
    project = ProjectSpec(project_id="demo", current_stage="requirements", design_context=None)
    context = DesignContext(erc_baseline={"errors": 0, "warnings": 0})
    task = AgentTask(
        task_id="T-2",
        assigned_agent="requirements",
        objective="requirements",
        prior_gate_findings=[
            "requirements gate: units normalized - PWR-001 is None, expected 'unit present'"
        ],
    )
    prompt = build_requirements_prompt(project, context, task, {})
    assert "REJECTED" in prompt
    assert "units normalized - PWR-001" in prompt
    # The contract the gate enforces is stated, not left to be guessed at.
    assert "PWR-NNN" in prompt and "TEST-NNN" in prompt


def test_the_repair_stage_inherits_the_contract_of_the_stage_it_corrects() -> None:
    """A repaired document has to satisfy the same schema and the same gate."""
    for stage in ("requirements", "architecture", "schematic_design", "verification"):
        target, repair = get_agent(stage), repair_spec(stage)
        assert repair.id == "repair"
        assert repair.output_model is target.output_model
        assert repair.reads == target.reads
        assert repair.read_only == target.read_only
        assert repair.mutates_design == target.mutates_design
        assert stage in repair.tags
        # It is a stage of its own, not the same agent asked twice.
        assert repair.prompt_builder is not target.prompt_builder


def test_the_repair_prompt_carries_the_rejected_document_and_the_findings() -> None:
    project = ProjectSpec(project_id="demo", current_stage="requirements", design_context=None)
    context = DesignContext(erc_baseline={"errors": 0, "warnings": 0})
    task = AgentTask(
        task_id="T-3",
        assigned_agent="requirements",
        objective="Correct the rejected Requirements document",
        prior_gate_findings=["requirements gate: units normalized - PWR-001 is None"],
        rejected_output={"requirements": [{"id": "PWR-001", "unit": ""}]},
    )
    prompt = build_repair_prompt(project, context, task, {})
    assert "rejected the requirements stage" in prompt
    assert "units normalized - PWR-001" in prompt
    assert '"PWR-001"' in prompt
    assert "Change only what the findings require" in prompt


def test_requirement_category_follows_its_identifier() -> None:
    """The ID prefix is the category; an agent's own word for it is normalized."""
    requirement = Requirement.model_validate(
        {
            "id": "test-001",
            "category": "quality",
            "statement": "ERC reports no errors",
            "value": 0,
            "unit": "errors",
            "source": "acceptance",
        }
    )
    assert (requirement.id, requirement.category) == ("TEST-001", "test")

    for identifier, category in (("PWR-002", "power"), ("IF-003", "interface"), ("MECH-004", "mechanical")):
        normalized = Requirement.model_validate(
            {
                "id": identifier,
                "category": "electrical",
                "statement": "s",
                "value": 1,
                "unit": "V",
                "source": "request",
            }
        )
        assert normalized.category == category

    with pytest.raises(ValidationError):
        Requirement.model_validate(
            {
                "id": "QUAL-001",
                "category": "power",
                "statement": "s",
                "value": 1,
                "unit": "V",
                "source": "request",
            }
        )


def test_requirements_schema_enumerates_the_four_categories() -> None:
    """The agent is handed the closed set in its own schema, not just in prose."""
    category = json_schema(RequirementsDoc)["properties"]["requirements"]["items"]["properties"][
        "category"
    ]
    assert category["enum"] == ["power", "interface", "mechanical", "test"]


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


def test_the_component_engineer_is_told_it_cannot_change_a_symbol() -> None:
    """The constraint the gate enforces has to be in the prompt that precedes it.

    This pipeline wires pins that exist; it has no intent that adds or replaces
    a schematic symbol. A run that needed a different connector was rejected for
    changing one, then passed the gate by keeping the old symbol beside the new
    part's footprint - a document describing a board nobody could build.
    """
    context = DesignContext(
        components=[
            {
                "reference": "J1",
                "value": "USB_B_Micro",
                "lib_id": "Connector:USB_B_Micro",
                "pins": [{"number": "1", "name": "VBUS", "electrical_type": "power_out"}],
            },
            {
                "reference": "#PWR01",
                "value": "GND",
                "lib_id": "power:GND",
                "pins": [{"number": "1", "name": "GND", "electrical_type": "power_in"}],
            },
        ],
        erc_baseline={"errors": 0, "warnings": 0},
    )
    project = ProjectSpec(project_id="demo", request="use USB-C", current_stage="components")
    task = AgentTask(task_id="c", assigned_agent="components", objective="pick parts")
    prompt = get_agent("components").build_prompt(project, task, {}, context)

    assert "cannot add a symbol" in prompt
    assert "J1 is Connector:USB_B_Micro" in prompt
    assert "do not pair" in prompt
    # Power flags and other generated references are not parts to select.
    assert "#PWR" not in prompt.split("The schematic already has:")[1].split("\n\n")[0]
    # A part the schematic cannot hold may be listed, but only marked as such.
    assert "proposed and not in the schematic" in prompt

    # The stage that wires those pins is told the same limit in its own terms:
    # a real run proposed connecting U3.3, and no U3 existed.
    wiring = get_agent("schematic_design").build_prompt(
        project,
        AgentTask(task_id="s", assigned_agent="schematic_design", objective="wire"),
        {},
        context,
    )
    assert "must appear in the pin table above" in wiring


def test_a_repair_of_the_release_is_given_the_manifest_too() -> None:
    """A repair cannot compute a SHA-256 any more than the first attempt could.

    Reaching the right hash through the rejected finding's `expected` field
    would work only for as long as that field survives the feedback clip.
    """
    project = ProjectSpec(project_id="demo", current_stage="qa_release")
    manifest = {"included_files": ["/tmp/demo.kicad_pcb"], "release_hash": "abc123"}
    task = AgentTask(
        task_id="r",
        assigned_agent="qa_release",
        objective="correct the release",
        rejected_output={"release_hash": "guessed"},
        release_manifest=manifest,
    )
    prompt = repair_spec("qa_release").build_prompt(project, task, {})
    assert "/tmp/demo.kicad_pcb" in prompt
    assert "abc123" in prompt

    # Every other stage's repair is unchanged - there is no manifest to show.
    plain = repair_spec("requirements").build_prompt(
        project,
        task.model_copy(update={"assigned_agent": "requirements", "release_manifest": None}),
        {},
    )
    assert "The release package is exactly these files" not in plain
