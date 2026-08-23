"""Deterministic team stage-gate and fallback coverage."""

from __future__ import annotations

import json
from pathlib import Path

from app.kicad.reader import ProjectState, read_project
from app.models import ErcReport
from app.team.checks import (
    RELEASE_CHECKLIST_ITEMS,
    check_architecture,
    check_components,
    check_layout,
    check_manufacturing,
    check_qa_release,
    check_requirements,
    check_schematic,
    check_simulation,
    check_verification,
)
from app.team.fallbacks import fallback_for, requirements_fallback
from app.team.profiles import get_profile
from app.team.schemas import (
    AgentTask,
    Architecture,
    ComponentSelection,
    DesignContext,
    PowerRequirements,
    ProjectSpec,
    RequirementsDoc,
    SimulationReport,
    VerificationFinding,
    VerificationReport,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _requirements() -> RequirementsDoc:
    return RequirementsDoc(
        requirements=[
            {
                "id": "PWR-001",
                "category": "power",
                "statement": "logic voltage",
                "value": 3.3,
                "unit": "V",
                "source": "request",
            }
        ],
        power={"input": "", "logic_voltage": "3.3V", "maximum_current_ma": None},
        interfaces=[],
        mechanical={"maximum_width_mm": None, "maximum_height_mm": None, "layers": None},
        constraints=[],
        acceptance_tests=[],
    )


def _project(name: str = "esp32_i2c_board") -> tuple[ProjectSpec, ProjectState]:
    directory = FIXTURES / name
    state = read_project(directory)
    context = DesignContext.from_project(
        state,
        ErcReport(ran=True),
        ErcReport(ran=True),
        directory / f"{name}.kicad_pcb",
    )
    return ProjectSpec(
        project_id=name,
        request="Use I2C on GPIO21 and GPIO22 at 3.3V",
        current_stage="team",
        design_context=context,
    ), state


def test_requirements_gate_passes_and_rejects_duplicate_or_missing_unit() -> None:
    document = _requirements()
    assert check_requirements(document, "rev-1").passed
    invalid = document.model_copy(
        update={
            "requirements": [
                document.requirements[0],
                document.requirements[0].model_copy(update={"id": "PWR-001", "unit": ""}),
            ]
        }
    )
    assert not check_requirements(invalid, "rev-1").passed


def test_captured_live_requirements_accept_qualifying_summary_prose() -> None:
    payload = json.loads((Path(__file__).parent / "fixtures" / "live_requirements.json").read_text())
    result = check_requirements(RequirementsDoc.model_validate(payload), "live-rev")
    assert result.passed, result.findings


def test_captured_live_requirements_survive_a_synonym_category() -> None:
    """A real run filed its TEST-00x requirements under the category "quality".

    That failed the gate three times over - once per return trip - on the word
    alone. The ID prefix now decides the category, so the same document is
    accepted and the TEST-backed acceptance tests are seen for what they are.
    """
    path = Path(__file__).parent / "fixtures" / "live_requirements_synonym_category.json"
    payload = json.loads(path.read_text())
    assert {requirement["category"] for requirement in payload["requirements"]} & {"quality"}
    document = RequirementsDoc.model_validate(payload)
    assert {requirement.category for requirement in document.requirements} == {
        "power",
        "interface",
        "mechanical",
        "test",
    }
    result = check_requirements(document, "live-rev")
    assert result.passed, result.findings


def test_prose_only_summary_is_a_warning_not_an_error() -> None:
    document = _requirements().model_copy(
        update={
            "power": PowerRequirements(
                input="", logic_voltage="regulated logic rail", maximum_current_ma=None
            )
        }
    )
    result = check_requirements(document, "rev-1")
    assert result.passed
    assert any(finding.severity == "warning" for finding in result.findings)


def test_conflicting_values_for_same_power_statement_fail() -> None:
    document = _requirements().model_copy(
        update={
            "requirements": [
                *_requirements().requirements,
                _requirements().requirements[0].model_copy(update={"id": "PWR-002", "value": 5}),
            ]
        }
    )
    result = check_requirements(document, "rev-1")
    assert not result.passed
    assert any(finding.rule == "conflicting requirements" for finding in result.findings)


def test_architecture_gate_passes_and_rejects_unknown_connection() -> None:
    passing = Architecture(
        blocks=[{"id": "A", "type": "source", "requirement_ids": ["PWR-001"]}],
        connections=[],
    )
    assert check_architecture(_requirements(), passing, "rev-1").passed is True
    failing = Architecture(
        blocks=[{"id": "A", "type": "source"}],
        connections=[{"from": "A", "to": "MISSING", "signal": "V"}],
    )
    assert check_architecture(_requirements(), failing, "rev-1").passed is False


def test_components_gate_uses_real_fixture_context_and_requires_provenance() -> None:
    project, _ = _project()
    context = project.design_context
    assert context is not None
    passing = ComponentSelection(components=[])
    assert check_components(passing, context, project_version="rev-1").passed
    failing = ComponentSelection(
        components=[
            {
                "reference_group": context.components[0].reference,
                "manufacturer_part": context.components[0].value,
                "quantity": 1,
                "symbol": context.components[0].lib_id,
                "footprint": "",
                "reason": "",
                "verified_constraints": [],
                "specifications": [
                    {
                        "parameter": "voltage",
                        "unit": "V",
                        "page_or_section": "electrical",
                        "minimum": "1",
                        "typical": "1",
                        "maximum": "1",
                    }
                ],
            }
        ]
    )
    assert check_components(failing, context, project_version="rev-1").passed is False


def test_schematic_gate_uses_fixture_state_and_erc() -> None:
    _, state = _project()
    assert check_schematic(state, state, ErcReport(ran=True), project_version="rev-1").passed
    assert not check_schematic(state, state, ErcReport(ran=False), project_version="rev-1").passed


def test_layout_gate_uses_real_fixture_board() -> None:
    board = FIXTURES / "esp32_i2c_board" / "esp32_i2c_board.kicad_pcb"
    assert check_layout(board, get_profile("generic_two_layer"), project_version="rev-1").passed
    assert (
        check_layout(
            board, get_profile("generic_two_layer"), ["not-on-board"], project_version="rev-1"
        ).passed
        is False
    )


def test_simulation_gate_passes_with_units_and_fails_without_assumptions() -> None:
    passing = SimulationReport(
        tests=[
            {
                "name": "rail",
                "expected": {"nominal": "3.3 V"},
                "measured_v": "unverified V",
                "status": "unverified",
                "source": "requirement PWR-001",
            }
        ],
        assumptions=["no model"],
    )
    assert check_simulation(passing, _requirements(), project_version="rev-1").passed
    unitless = passing.model_copy(update={"tests": [passing.tests[0].model_copy(update={"measured_v": 3.3})]})
    assert not check_simulation(unitless, _requirements(), project_version="rev-1").passed
    unknown_requirement = passing.model_copy(
        update={"tests": [passing.tests[0].model_copy(update={"source": "requirement PWR-999"})]}
    )
    assert not check_simulation(unknown_requirement, _requirements(), project_version="rev-1").passed
    assert not check_simulation(SimulationReport(tests=[]), project_version="rev-1").passed


def test_verification_gate_passes_and_rejects_missing_evidence() -> None:
    passing = VerificationReport(
        requirements_total=1,
        requirements_passed=1,
        requirements_failed=0,
        requirements_unverified=0,
        critical_findings=[],
        decision="passed",
    )
    assert check_verification(passing, "rev-1").passed
    failing = passing.model_copy(
        update={
            "critical_findings": [VerificationFinding(requirement_id="PWR-001", finding="bad", evidence={})]
        }
    )
    assert not check_verification(failing, "rev-1").passed


def test_manufacturing_gate_refuses_unknown_profile() -> None:
    report = {
        "manufacturer_profile": "unknown",
        "dfm_status": "passed",
        "findings": [],
        "fabrication_ready": True,
    }
    from app.team.schemas import ManufacturingReport

    assert not check_manufacturing(ManufacturingReport(**report), "unknown", "rev-1").passed
    known = ManufacturingReport(
        manufacturer_profile="generic_two_layer",
        dfm_status="passed",
        findings=[],
        fabrication_ready=True,
        profile_provenance=get_profile("generic_two_layer").provenance,
        profile_rules={
            "minimum_trace_width_mm": 0.15,
            "minimum_spacing_mm": 0.15,
            "minimum_drill_mm": 0.2,
            "copper_to_edge_clearance_mm": 0.3,
            "supported_layer_count": 2.0,
        },
    )
    assert check_manufacturing(known, "generic_two_layer", "rev-1").passed


def test_qa_release_gate_checks_real_hashes(tmp_path: Path) -> None:
    file = tmp_path / "board.kicad_pcb"
    file.write_text("board", encoding="utf-8")
    release = fallback_for(
        "qa_release",
        ProjectSpec(project_id="demo", current_stage="qa"),
        AgentTask(task_id="qa", assigned_agent="qa_release", objective="release"),
        {"project_files": [str(file)]},
    )
    release = release.model_copy(update={"checklist": {key: True for key in RELEASE_CHECKLIST_ITEMS}})
    assert check_qa_release(release, [file], "rev-1").passed
    assert not check_qa_release(release.model_copy(update={"release_hash": "bad"}), [file], "rev-1").passed


def test_fallbacks_do_not_invent_requirements_or_design_values() -> None:
    project = ProjectSpec(project_id="empty", current_stage="requirements", request="")
    task = AgentTask(task_id="r", assigned_agent="requirements", objective="parse")
    output = requirements_fallback(project, task, {})
    assert output.requirements == []
    assert output.power.logic_voltage == ""
    assert output.mechanical.maximum_width_mm is None
    assert output.mechanical.layers is None
    assert "USB" not in output.power.input.upper()
