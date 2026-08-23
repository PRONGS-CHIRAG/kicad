"""Deterministic team stage-gate and fallback coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
from app.team.registry import get_agent
from app.team.schemas import (
    AgentTask,
    Architecture,
    ComponentSelection,
    ComponentSpecification,
    DesignContext,
    InterfaceRequirement,
    LayoutProposal,
    PowerRequirements,
    ProjectSpec,
    Requirement,
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


def test_second_captured_live_requirements_accept_category_synonyms() -> None:
    payload = json.loads((Path(__file__).parent / "fixtures" / "live_requirements_synonyms.json").read_text())
    result = check_requirements(RequirementsDoc.model_validate(payload), "live-synonyms-rev")
    assert result.passed, result.findings


def test_captured_seventh_live_requirements_accept_board_dimensions() -> None:
    payload = json.loads((Path(__file__).parent / "fixtures" / "live_requirements_seventh.json").read_text())
    result = check_requirements(RequirementsDoc.model_validate(payload), "live-seventh-rev")
    assert result.passed, result.findings


@pytest.mark.parametrize(
    ("category", "requirement_id"),
    [
        ("supply", "PWR-001"),
        ("communication", "IF-001"),
        ("physical", "MECH-001"),
        ("fabrication", "MFG-001"),
        ("budget", "COST-001"),
        ("thermal", "TEMP-001"),
        ("validation", "TEST-001"),
        ("acceptance", "TEST-001"),
        ("acceptance/test", "TEST-001"),
        ("test", "TEST-001"),
    ],
)
def test_requirement_category_synonyms_use_canonical_prefix(category: str, requirement_id: str) -> None:
    document = _requirements().model_copy(
        update={
            "requirements": [
                _requirements()
                .requirements[0]
                .model_copy(update={"category": category, "id": requirement_id})
            ]
        }
    )
    result = check_requirements(document, "rev-1")
    assert result.passed, result.findings


def test_unknown_requirement_category_is_a_warning() -> None:
    document = _requirements().model_copy(
        update={
            "requirements": [
                _requirements()
                .requirements[0]
                .model_copy(update={"category": "electromagnetic", "id": "PWR-001"})
            ],
            "acceptance_tests": ["verify the board"],
        }
    )
    result = check_requirements(document, "rev-1")
    assert result.passed
    assert any(finding.severity == "warning" for finding in result.findings)


def test_known_category_with_wrong_prefix_still_fails() -> None:
    document = _requirements().model_copy(
        update={
            "requirements": [
                _requirements().requirements[0].model_copy(update={"category": "supply", "id": "IF-001"})
            ]
        }
    )
    result = check_requirements(document, "rev-1")
    assert not result.passed
    assert any(finding.rule == "requirement ID matches category" for finding in result.findings)


def test_usb_type_c_protocol_spelling_is_normalized() -> None:
    document = _requirements().model_copy(
        update={
            "requirements": [
                _requirements()
                .requirements[0]
                .model_copy(
                    update={
                        "category": "communication",
                        "id": "IF-001",
                        "statement": "USB-C power input",
                        "value": "USB-C",
                        "unit": "connector type",
                    }
                )
            ],
            "interfaces": [InterfaceRequirement(type="USB Type-C power input", voltage="", devices=1)],
        }
    )
    assert check_requirements(document, "rev-1").passed


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


def test_same_statement_different_dimensions_do_not_conflict() -> None:
    document = _requirements().model_copy(
        update={
            "requirements": [
                *_requirements().requirements,
                Requirement(
                    id="MECH-001",
                    category="mechanical",
                    statement="Keep the finished PCB within the specified board outline.",
                    value=110,
                    unit="maximum width mm",
                ),
                Requirement(
                    id="MECH-002",
                    category="mechanical",
                    statement="Keep the finished PCB within the specified board outline.",
                    value=65,
                    unit="maximum height mm",
                ),
            ]
        }
    )
    result = check_requirements(document, "rev-1")
    assert result.passed, result.findings


def test_negative_requirement_value_fails() -> None:
    document = _requirements().model_copy(
        update={"requirements": [_requirements().requirements[0].model_copy(update={"value": -1})]}
    )
    result = check_requirements(document, "rev-1")
    assert not result.passed
    assert any(finding.rule == "range validation" for finding in result.findings)


def test_gated_agent_prompts_state_the_vocabulary_they_emit() -> None:
    project, _ = _project()
    task_inputs = {
        "requirements": (
            "power=PWR",
            "interface=IF",
            "mechanical=MECH",
            "manufacturing=MFG",
            "cost=COST",
            "temperature=TEMP",
            "acceptance/test=TEST",
            "PREFIX-NNN",
        ),
        "components": (
            "bare Library:Name library identifier",
            "no parentheses, commentary, or alternatives",
            "reason or verified_constraints",
        ),
        "schematic_design": (
            "Power symbols and PWR_FLAG symbols are net markers",
            "Connecting an already-netted ordinary pin to a new net moves its existing label",
        ),
        "simulation": (
            "pass, failed, or unverified",
            "Free-form expected measurement values must include units",
            "required_ma, available_ma, margin_percent, and measured_v already encode their units",
            "pass or failed verdict",
            "unverified tests must state a concise explicit reason",
            "not a silent escape hatch",
        ),
        "verification": (
            "severity error, warning, or info",
            "decision status passed, failed, or unverified",
        ),
        "manufacturing": (
            "dfm_status passed, failed, or unverified",
            "finding severity must be error, warning, or info",
        ),
        "qa_release": ("ready for engineering review", "needs_human_review", "approved"),
    }
    for agent_id, expected in task_inputs.items():
        prompt = get_agent(agent_id).build_prompt(
            project,
            AgentTask(task_id=f"{agent_id}-prompt", assigned_agent=agent_id, objective="check"),
            {},
        )
        assert all(fragment in prompt for fragment in expected), agent_id


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


def test_captured_live_architecture_passes_connectivity_and_budget_gate() -> None:
    requirements = RequirementsDoc.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_architecture_requirements.json").read_text())
    )
    architecture = Architecture.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_architecture.json").read_text())
    )
    result = check_architecture(requirements, architecture, "live-architecture-rev")
    assert result.passed, result.findings


def test_fourth_live_architecture_treats_converter_overload_as_advisory() -> None:
    requirements = RequirementsDoc.model_validate(
        json.loads(
            (Path(__file__).parent / "fixtures" / "live_architecture_fourth_requirements.json").read_text()
        )
    )
    architecture = Architecture.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_architecture_fourth.json").read_text())
    )
    result = check_architecture(requirements, architecture, "fourth-live-architecture-rev")
    assert result.passed, result.findings
    assert any(
        finding.rule == "power budget"
        and finding.actual == 620.0
        and finding.severity == "warning"
        and "converter declaration is advisory" in str(finding.expected)
        for finding in result.findings
    )


def test_fifth_live_architecture_passes_identifier_connectivity_gate() -> None:
    requirements = RequirementsDoc.model_validate(
        json.loads(
            (Path(__file__).parent / "fixtures" / "live_architecture_fifth_requirements.json").read_text()
        )
    )
    architecture = Architecture.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_architecture_fifth.json").read_text())
    )
    result = check_architecture(requirements, architecture, "fifth-live-architecture-rev")
    assert result.passed, result.findings


def test_seventh_live_architecture_passes_voltage_annotated_nets() -> None:
    requirements = RequirementsDoc.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_requirements_seventh.json").read_text())
    )
    architecture = Architecture.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_architecture_seventh.json").read_text())
    )
    result = check_architecture(requirements, architecture, "seventh-live-architecture-rev")
    assert result.passed, result.findings


def test_eighth_live_architecture_treats_temperature_mapping_as_advisory() -> None:
    requirements = RequirementsDoc.model_validate(
        json.loads(
            (Path(__file__).parent / "fixtures" / "live_architecture_eighth_requirements.json").read_text()
        )
    )
    architecture = Architecture.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_architecture_eighth.json").read_text())
    )
    result = check_architecture(requirements, architecture, "eighth-live-architecture-rev")
    assert result.passed, result.findings
    assert any(
        finding.rule == "required input connectivity" and finding.severity == "warning"
        for finding in result.findings
    )
    assert any(
        finding.rule == "required output connectivity" and finding.severity == "warning"
        for finding in result.findings
    )
    assert any(
        finding.rule == "requirements map to blocks"
        and finding.actual == "TEMP-001"
        and finding.severity == "warning"
        for finding in result.findings
    )
    assert any(
        finding.rule == "power budget"
        and finding.actual == 605.0
        and finding.severity == "warning"
        and "converter declaration is advisory" in str(finding.expected)
        for finding in result.findings
    )


def test_twelfth_live_architecture_passes_per_rail_budget_and_temperature_mapping() -> None:
    requirements = RequirementsDoc.model_validate(
        json.loads(
            (Path(__file__).parent / "fixtures" / "live_architecture_twelfth_requirements.json").read_text()
        )
    )
    architecture = Architecture.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_architecture_twelfth.json").read_text())
    )
    result = check_architecture(requirements, architecture, "twelfth-live-architecture-rev")
    assert result.passed, result.findings
    assert all(
        finding.severity != "error" for finding in result.findings if finding.rule.startswith("power budget")
    )


def test_voltage_suffix_is_an_annotation_but_filtered_rail_stays_distinct() -> None:
    passing = Architecture(
        blocks=[
            {"id": "source", "type": "source", "requirement_ids": ["PWR-001"]},
            {"id": "load", "type": "load", "required_inputs": ["VBUS"]},
        ],
        connections=[{"from": "source", "to": "load", "signal": "VBUS 5 V power"}],
    )
    assert check_architecture(_requirements(), passing, "rev-1").passed

    filtered = passing.model_copy(
        update={"connections": [passing.connections[0].model_copy(update={"signal": "VBUS_FILT"})]}
    )
    result = check_architecture(_requirements(), filtered, "rev-1")
    assert result.passed
    assert any(
        finding.rule == "required input connectivity"
        and finding.actual == "VBUS"
        and finding.severity == "warning"
        for finding in result.findings
    )


def test_architecture_global_nets_do_not_require_point_to_point_edges() -> None:
    architecture = Architecture(
        blocks=[
            {
                "id": "source",
                "type": "source",
                "requirement_ids": ["PWR-001"],
                "required_outputs": ["GND"],
            },
            {
                "id": "distribution",
                "type": "power_distribution",
                "required_inputs": ["GND"],
                "required_outputs": ["+3V3", "GND"],
            },
            {"id": "consumer", "type": "sensor", "required_inputs": ["GND"]},
            {
                "id": "constraints",
                "type": "mechanical zoning",
                "required_inputs": ["2-layer stackup"],
                "required_outputs": ["USB-C edge placement zone"],
            },
        ],
        connections=[
            {"from": "source", "to": "distribution", "signal": "GND return"},
            {"from": "distribution", "to": "consumer", "signal": "+3V3 rail"},
        ],
    )
    result = check_architecture(_requirements(), architecture, "rev-1")
    assert result.passed, result.findings


def test_architecture_missing_required_input_is_advisory() -> None:
    architecture = Architecture(
        blocks=[
            {
                "id": "load",
                "type": "sensor",
                "required_inputs": ["I2C_SDA"],
                "requirement_ids": ["PWR-001"],
            }
        ],
        connections=[],
    )
    result = check_architecture(_requirements(), architecture, "rev-1")
    assert result.passed
    assert any(
        finding.rule == "required input connectivity" and finding.severity == "warning"
        for finding in result.findings
    )


def test_architecture_identifier_matching_rejects_spurious_token_overlap() -> None:
    architecture = Architecture(
        blocks=[
            {
                "id": "source",
                "type": "source",
                "requirement_ids": ["PWR-001"],
            },
            {"id": "load", "type": "load", "required_inputs": ["POWER_GOOD"]},
        ],
        connections=[{"from": "source", "to": "load", "signal": "USB_POWER"}],
    )
    result = check_architecture(_requirements(), architecture, "rev-1")
    assert result.passed
    assert any(
        finding.rule == "required input connectivity" and finding.severity == "warning"
        for finding in result.findings
    )


def test_architecture_unmatched_power_rail_is_only_a_warning() -> None:
    architecture = Architecture(
        blocks=[
            {
                "id": "load",
                "type": "load",
                "power_required_ma": 10,
                "required_inputs": ["AUX_RAIL"],
                "requirement_ids": ["PWR-001"],
            },
        ],
        connections=[],
    )
    result = check_architecture(_requirements(), architecture, "rev-1")
    assert result.passed
    assert any(
        finding.rule == "power budget rail supplier"
        and finding.actual == "AUX_RAIL"
        and finding.severity == "warning"
        for finding in result.findings
    )


def test_architecture_non_converter_voltage_mismatch_fails() -> None:
    architecture = Architecture(
        blocks=[
            {"id": "source", "type": "source", "output_voltage": "5 V"},
            {"id": "load", "type": "load", "input_voltage": "3.3 V"},
        ],
        connections=[{"from": "source", "to": "load", "signal": "VCC"}],
    )
    result = check_architecture(_requirements(), architecture, "rev-1")
    assert not result.passed
    assert any(finding.rule == "connected block voltage compatibility" for finding in result.findings)


def test_architecture_unmapped_functional_requirement_fails() -> None:
    result = check_architecture(
        _requirements(),
        Architecture(blocks=[{"id": "block", "type": "source"}], connections=[]),
        "rev-1",
    )
    assert not result.passed
    assert any(finding.rule == "requirements map to blocks" for finding in result.findings)


def test_architecture_total_power_draw_exceeding_rail_capacity_fails() -> None:
    document = _requirements().model_copy(
        update={
            "requirements": [
                _requirements()
                .requirements[0]
                .model_copy(update={"id": "PWR-003", "value": 500, "unit": "mA"})
            ]
        }
    )
    architecture = Architecture(
        blocks=[
            {
                "id": "source",
                "type": "source",
                "power_available_ma": 500,
                "required_outputs": ["VBUS"],
                "requirement_ids": ["PWR-003"],
            },
            {
                "id": "first",
                "type": "load",
                "power_required_ma": 300,
                "required_inputs": ["VBUS"],
            },
            {"id": "second", "type": "load", "power_required_ma": 350, "required_inputs": ["VBUS"]},
        ],
        connections=[],
    )
    result = check_architecture(document, architecture, "rev-1")
    assert not result.passed
    assert any(finding.rule == "power budget" for finding in result.findings)


def test_architecture_marginal_power_overshoot_is_a_warning() -> None:
    document = _requirements().model_copy(
        update={
            "requirements": [
                _requirements()
                .requirements[0]
                .model_copy(update={"id": "PWR-003", "value": 600, "unit": "mA"})
            ]
        }
    )
    architecture = Architecture(
        blocks=[
            {
                "id": "source",
                "type": "source",
                "power_available_ma": 600,
                "required_outputs": ["VBUS"],
            },
            {
                "id": "load",
                "type": "load",
                "power_required_ma": 602.74,
                "required_inputs": ["VBUS"],
                "requirement_ids": ["PWR-003"],
            },
        ],
        connections=[],
    )
    result = check_architecture(document, architecture, "rev-1")
    assert result.passed
    assert any(
        finding.rule == "power budget"
        and finding.severity == "warning"
        and "marginal" in str(finding.expected)
        for finding in result.findings
    )


def test_architecture_dp_and_dm_alias_usb_differential_signals() -> None:
    architecture = Architecture(
        blocks=[
            {"id": "source", "type": "source", "requirement_ids": ["PWR-001"]},
            {"id": "load", "type": "load", "required_inputs": ["DP", "DM"]},
        ],
        connections=[
            {"from": "source", "to": "load", "signal": "D+"},
            {"from": "source", "to": "load", "signal": "D-"},
        ],
    )
    result = check_architecture(_requirements(), architecture, "rev-1")
    assert result.passed, result.findings


def test_architecture_board_level_requirement_mapping_is_only_a_warning() -> None:
    document = _requirements().model_copy(
        update={
            "requirements": [
                _requirements()
                .requirements[0]
                .model_copy(update={"category": "mechanical", "id": "MECH-001"})
            ]
        }
    )
    result = check_architecture(
        document,
        Architecture(blocks=[{"id": "block", "type": "source"}], connections=[]),
        "rev-1",
    )
    assert result.passed
    assert any(
        finding.rule == "requirements map to blocks" and finding.severity == "warning"
        for finding in result.findings
    )


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


def test_captured_ninth_live_components_accept_annotations_and_partial_bounds() -> None:
    payload = json.loads((Path(__file__).parent / "fixtures" / "live_components_ninth.json").read_text())
    selection = ComponentSelection.model_validate(payload)
    project, _ = _project("esp32_i2c_demo")
    assert project.design_context is not None
    result = check_components(selection, project.design_context, project_version="ninth-live")
    assert result.passed, result.findings
    assert any(
        finding.rule == "numeric specification bound missing" and finding.severity == "warning"
        for finding in result.findings
    )
    assert any(
        finding.rule == "component references exist" and finding.severity == "warning"
        for finding in result.findings
    )


def test_components_require_bare_identifiers_and_real_specification_evidence() -> None:
    project, _ = _project()
    context = project.design_context
    assert context is not None
    malformed_symbol = ComponentSelection(
        components=[
            {
                "reference_group": "J1",
                "manufacturer_part": "part",
                "quantity": 1,
                "symbol": "not a library identifier",
                "footprint": "Connector_USB:USB_C",
                "reason": "",
                "verified_constraints": [],
            }
        ]
    )
    result = check_components(malformed_symbol, context, project_version="rev-1")
    assert not result.passed
    assert any(finding.rule == "symbol identifier is well formed" for finding in result.findings)

    missing_provenance = malformed_symbol.model_copy(
        update={
            "components": [
                malformed_symbol.components[0].model_copy(
                    update={
                        "symbol": "Device:R",
                        "specifications": [
                            ComponentSpecification(
                                parameter="resistance",
                                unit="ohm",
                                source="",
                                page_or_section="",
                                minimum=1,
                                typical="-",
                                maximum="-",
                            )
                        ],
                    }
                )
            ]
        }
    )
    result = check_components(missing_provenance, context, project_version="rev-1")
    assert not result.passed
    assert any(finding.rule == "numeric specification provenance" for finding in result.findings)

    no_numeric_bound = missing_provenance.model_copy(
        update={
            "components": [
                missing_provenance.components[0].model_copy(
                    update={
                        "specifications": [
                            ComponentSpecification(
                                parameter="resistance",
                                unit="ohm",
                                source="datasheet",
                                page_or_section="table 1",
                                minimum="n/a",
                                typical="none",
                                maximum="not specified",
                            )
                        ],
                    }
                )
            ]
        }
    )
    result = check_components(no_numeric_bound, context, project_version="rev-1")
    assert not result.passed
    assert any(finding.rule == "numeric specification bounds" for finding in result.findings)


def test_components_parse_reference_groups_and_allow_symbol_substitution() -> None:
    project, _ = _project()
    context = project.design_context
    assert context is not None
    selection = ComponentSelection(
        components=[
            {
                "reference_group": "J1, U1 (CC1, CC2)",
                "manufacturer_part": "part",
                "quantity": 1,
                "symbol": "Connector:USB_C",
                "footprint": "Connector_USB:USB_C",
                "reason": "",
                "verified_constraints": [],
            }
        ]
    )
    result = check_components(selection, context, project_version="rev-1")
    assert result.passed
    assert any(
        finding.rule == "existing schematic symbol matches"
        and finding.kicad_object == "J1"
        and finding.severity == "warning"
        for finding in result.findings
    )

    new_proposal = selection.model_copy(
        update={
            "components": [
                selection.components[0].model_copy(update={"reference_group": "U99 (new proposal)"})
            ]
        }
    )
    result = check_components(new_proposal, context, project_version="rev-1")
    assert result.passed
    assert any(
        finding.rule == "component references exist"
        and finding.actual == "U99"
        and finding.severity == "warning"
        for finding in result.findings
    )

    absent_reference_with_current_word = new_proposal.model_copy(
        update={
            "components": [
                new_proposal.components[0].model_copy(
                    update={"reason": "Use U99 as a local part to absorb current bursts"}
                )
            ]
        }
    )
    result = check_components(absent_reference_with_current_word, context, project_version="rev-1")
    assert result.passed
    assert any(
        finding.rule == "component references exist"
        and finding.actual == "U99"
        and finding.severity == "warning"
        for finding in result.findings
    )


def test_captured_eleventh_live_components_pass_with_fixture_context() -> None:
    payload = json.loads((Path(__file__).parent / "fixtures" / "live_components_eleventh.json").read_text())
    selection = ComponentSelection.model_validate(payload)
    project, _ = _project("esp32_i2c_demo")
    assert project.design_context is not None
    result = check_components(selection, project.design_context, project_version="eleventh-live")
    assert result.passed, result.findings
    assert any(
        finding.rule == "component references exist"
        and finding.actual == "C3"
        and finding.severity == "warning"
        for finding in result.findings
    )


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


def test_layout_gate_walks_footprint_pads_and_distinguishes_schematic_only_nets(
    tmp_path: Path,
) -> None:
    board = FIXTURES / "esp32_i2c_demo" / "esp32_i2c_demo.kicad_pcb"
    required = ["+3V3", "GND", "USB_D+", "USB_D-", "VBUS"]
    assert check_layout(board, get_profile("generic_two_layer"), required, project_version="rev-1").passed

    live_layout = LayoutProposal.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_pcb_layout_fourteenth.json").read_text())
    )
    schematic_only = check_layout(
        board,
        get_profile("generic_two_layer"),
        live_layout.critical_nets,
        schematic_nets=["SDA", "SCL"],
        project_version="rev-1",
    )
    assert schematic_only.passed
    assert {
        finding.actual
        for finding in schematic_only.findings
        if finding.rule == "required net connected" and finding.severity == "warning"
    } == {"SDA", "SCL"}
    assert all(
        finding.rule == "required net connected"
        and finding.severity == "warning"
        and "MVP does not update the board netlist or route" in finding.expected
        for finding in schematic_only.findings
        if finding.actual in {"SDA", "SCL"}
    )

    board_with_unpadded_net = tmp_path / "board.kicad_pcb"
    board_with_unpadded_net.write_text(
        board.read_text().replace(
            '(net 5 "VBUS")',
            '(net 5 "VBUS")\n\t(net 6 "BOARD_ONLY")',
            1,
        ),
        encoding="utf-8",
    )
    unpadded = check_layout(
        board_with_unpadded_net,
        get_profile("generic_two_layer"),
        ["BOARD_ONLY"],
        project_version="rev-1",
    )
    assert not unpadded.passed
    assert any(
        finding.rule == "required net connected"
        and finding.actual == "BOARD_ONLY"
        and finding.severity == "error"
        for finding in unpadded.findings
    )


def test_simulation_gate_passes_with_units_and_fails_without_assumptions() -> None:
    passing = SimulationReport(
        tests=[
            {
                "name": "rail",
                "expected": {"nominal": "3.3 V"},
                "measured_v": "unverified V",
                "status": "UNVERIFIED; no model",
                "source": "requirement PWR-001",
            }
        ],
        assumptions=["no model"],
    )
    assert check_simulation(passing, _requirements(), project_version="rev-1").passed
    unitless = passing.model_copy(update={"tests": [passing.tests[0].model_copy(update={"measured_v": 3.3})]})
    assert not check_simulation(unitless, _requirements(), project_version="rev-1").passed
    unknown_requirement = passing.model_copy(
        update={
            "tests": [passing.tests[0].model_copy(update={"status": "pass", "source": "requirement PWR-999"})]
        }
    )
    assert not check_simulation(unknown_requirement, _requirements(), project_version="rev-1").passed
    empty_reason = passing.model_copy(
        update={"tests": [passing.tests[0].model_copy(update={"measured_v": ""})]}
    )
    assert not check_simulation(empty_reason, _requirements(), project_version="rev-1").passed
    missing_source_pass = passing.model_copy(
        update={"tests": [passing.tests[0].model_copy(update={"status": "pass", "source": None})]}
    )
    assert not check_simulation(missing_source_pass, _requirements(), project_version="rev-1").passed
    failed = passing.tests[0].model_copy(update={"status": "failed"})
    failed_report = passing.model_copy(update={"tests": [failed]})
    assert not check_simulation(failed_report, _requirements(), project_version="rev-1").passed
    missing_source_failed = failed_report.model_copy(
        update={"tests": [failed.model_copy(update={"source": None})]}
    )
    assert not check_simulation(missing_source_failed, _requirements(), project_version="rev-1").passed
    assert not check_simulation(SimulationReport(tests=[]), project_version="rev-1").passed


def test_thirteenth_live_simulation_keeps_unverified_reasons_and_failed_tests_strict() -> None:
    report = SimulationReport.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_simulation_thirteenth.json").read_text())
    )
    requirements = RequirementsDoc.model_validate(
        json.loads(
            (Path(__file__).parent / "fixtures" / "live_architecture_twelfth_requirements.json").read_text()
        )
    )
    unverified_without_source = report.model_copy(
        update={
            "tests": [
                test.model_copy(update={"source": None})
                if "LED" in test.name or "divider" in test.name
                else test
                for test in report.tests
            ]
        }
    )
    result = check_simulation(unverified_without_source, requirements, project_version="thirteenth-live")
    assert not result.passed
    unverified = [
        test for test in unverified_without_source.tests if test.status.lower().startswith("unverified")
    ]
    assert unverified
    assert all(test.source is None for test in unverified if "LED" in test.name or "divider" in test.name)
    assert not any(
        finding.rule == "simulation range traceability"
        and finding.kicad_object in {test.name for test in unverified}
        for finding in result.findings
    )
    assert any(
        finding.rule == "simulation test status"
        and "VBUS draw vs USB-C default source budget" in finding.kicad_object
        and finding.severity == "error"
        for finding in result.findings
    )


def test_fourteenth_live_simulation_accepts_named_units_but_checks_free_form_units() -> None:
    report = SimulationReport.model_validate(
        json.loads((Path(__file__).parent / "fixtures" / "live_simulation_fourteenth.json").read_text())
    )
    result = check_simulation(report, project_version="fourteenth-live")
    assert not any(finding.rule == "simulation values carry units" for finding in result.findings)
    assert not result.passed
    assert any(
        finding.rule == "simulation test status"
        and finding.actual == "failed"
        and finding.severity == "error"
        for finding in result.findings
    )

    unitless_expected = SimulationReport(
        tests=[
            {
                "name": "free-form rail measurement",
                "expected": {"nominal": 3.3},
                "measured_v": "3.3 V",
                "status": "pass",
                "source": "datasheet rail specification",
            }
        ],
        assumptions=["closed-form test"],
    )
    unitless_result = check_simulation(unitless_expected, project_version="unitless-expected")
    assert any(finding.rule == "simulation values carry units" for finding in unitless_result.findings)


def test_architecture_voltage_formatting_is_not_a_gate_failure() -> None:
    architecture = Architecture(
        blocks=[
            {"id": "source", "type": "source", "requirement_ids": ["PWR-001"], "output_voltage": "3.3V"},
            {"id": "load", "type": "load", "input_voltage": "3.3 V"},
        ],
        connections=[{"from": "source", "to": "load", "signal": "logic"}],
    )
    assert check_architecture(_requirements(), architecture, "rev-1").passed


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
        dfm_status="PASS",
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
