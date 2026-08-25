"""Deterministic team stage-gate and fallback coverage."""

from __future__ import annotations

import json
from pathlib import Path

from app.kicad.board import copper_edge_clearance, load
from app.kicad.reader import ProjectState, read_project
from app.models import ErcReport, Violation
from app.team.checks import (
    RELEASE_CHECKLIST_ITEMS,
    _clearance_advice,
    release_files,
    release_hash,
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
    ReleaseRecord,
    RequirementsDoc,
    SelectedComponent,
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


def _architecture(blocks: list[dict], connections: list[dict]) -> Architecture:
    return Architecture(blocks=blocks, connections=connections)


def test_a_block_that_declares_ports_must_be_wired_to_something() -> None:
    """The old rule subtracted requirement IDs from signal names, so it could
    never pass. What it should have been asking is whether the block is wired.

    Wired in one direction is enough: a USB connector's input comes from the
    host, and there is no on-board block to name as its source. Requiring both
    directions failed every boundary block on principle, so the missing side is
    a warning and only a block wired to nothing at all is an error.
    """
    isolated = _architecture(
        [
            {
                "id": "a",
                "type": "power",
                "requirement_ids": ["PWR-001"],
                "required_inputs": ["VBUS"],
                "required_outputs": ["+3V3"],
            }
        ],
        [],
    )
    result = check_architecture(_requirements(), isolated, "rev-1")
    assert not result.passed
    assert [finding.rule for finding in result.findings] == ["declared ports are connected"]

    wired = _architecture(
        [
            {"id": "a", "type": "power", "requirement_ids": ["PWR-001"], "required_outputs": ["+3V3"]},
            {"id": "b", "type": "mcu", "requirement_ids": ["PWR-001"], "required_inputs": ["+3V3"]},
        ],
        [{"from": "a", "to": "b", "signal": "+3V3 rail"}],
    )
    assert check_architecture(_requirements(), wired, "rev-1").passed

    # A connector fed from off-board: wired outward only, reported not gated.
    boundary = _architecture(
        [
            {
                "id": "j1",
                "type": "connector",
                "requirement_ids": ["PWR-001"],
                "required_inputs": ["VBUS 5 V from host"],
                "required_outputs": ["VBUS"],
            },
            {"id": "b", "type": "mcu", "requirement_ids": ["PWR-001"], "required_inputs": ["VBUS"]},
        ],
        [{"from": "j1", "to": "b", "signal": "VBUS 5 V power"}],
    )
    edge = check_architecture(_requirements(), boundary, "rev-1")
    assert edge.passed
    assert [finding.severity for finding in edge.findings] == ["warning"]


def test_the_power_budget_compares_a_block_against_what_feeds_it() -> None:
    """`power_available_ma` is what a block passes on, not what reaches it.

    A leaf consumer honestly reports 0 - it supplies nothing downstream - and
    comparing its own draw against that put every consumer in a real design over
    budget. What the rule is about is whether its suppliers can cover the draw.
    """

    def gate(supply: float, draw: float) -> tuple[bool, list[str]]:
        result = check_architecture(
            _requirements(),
            _architecture(
                [
                    {
                        "id": "ldo",
                        "type": "power",
                        "requirement_ids": ["PWR-001"],
                        "required_outputs": ["+3V3"],
                        "power_available_ma": supply,
                    },
                    {
                        "id": "mcu",
                        "type": "mcu",
                        "requirement_ids": ["PWR-001"],
                        "required_inputs": ["+3V3"],
                        "power_required_ma": draw,
                        "power_available_ma": 0.0,
                    },
                ],
                [{"from": "ldo", "to": "mcu", "signal": "+3V3 rail"}],
            ),
            "rev-1",
        )
        return result.passed, [finding.rule for finding in result.findings if finding.severity == "error"]

    # The consumer's own 0 mA output no longer condemns it.
    assert gate(500.0, 450.0) == (True, [])
    # A supplier that genuinely cannot cover the draw still fails.
    assert gate(300.0, 450.0) == (False, ["power budget"])


def test_captured_live_architecture_is_not_blocked_by_boundary_or_leaf_blocks() -> None:
    """The document a real run produced, rejected eight times before agent 4 ran.

    Three were boundary blocks fed from off-board, one a sensor answering on the
    bus that reaches it, and four were consumers reporting that they pass no
    current onward. None of them is a design a human would refuse to build.
    """
    directory = Path(__file__).parent / "fixtures"
    document = RequirementsDoc.model_validate(
        json.loads((directory / "live_requirements_boundary.json").read_text())
    )
    architecture = Architecture.model_validate(
        json.loads((directory / "live_architecture_boundary_blocks.json").read_text())
    )
    result = check_architecture(document, architecture, "live-rev")
    assert result.passed, [finding.model_dump() for finding in result.findings]
    assert all(finding.severity == "warning" for finding in result.findings)


def test_block_voltage_compatibility_gates_overvoltage_and_nothing_else() -> None:
    """Only a higher voltage driven into a lower-voltage input breaks hardware.

    A connection is `{from, to, signal}` with no direction semantics, so a
    ground return, a data pair and a shared rail all look like a mismatch to a
    string comparison. Each of those is reported at most as a warning.
    """

    def gate(source: str, target: str, signal: str) -> tuple[bool, list[str]]:
        result = check_architecture(
            _requirements(),
            _architecture(
                [
                    {"id": "s", "type": "power", "requirement_ids": ["PWR-001"], "output_voltage": source},
                    {"id": "t", "type": "mcu", "requirement_ids": ["PWR-001"], "input_voltage": target},
                ],
                [{"from": "s", "to": "t", "signal": signal}],
            ),
            "rev-1",
        )
        return result.passed, [finding.severity for finding in result.findings]

    assert gate("5 V", "3.3 V", "+5V rail") == (False, ["error"])
    assert gate("3.3 V +/-5 %", "3.3 V", "+3V3 rail") == (True, [])
    assert gate("3.3 V", "0 V", "GND return") == (True, [])
    assert gate("5 V", "3.3 V", "USB_D+ / USB_D- differential pair") == (True, [])
    assert gate("3.3 V", "5 V", "+3V3 rail") == (True, ["warning"])
    assert gate("regulated logic rail", "3.3 V", "+3V3 rail") == (True, ["warning"])


def test_captured_live_architecture_is_not_blocked_by_shared_rails() -> None:
    """The pair of documents a real run produced, which the gate rejected 34 times.

    Every finding was a ground return, a data pair or a rail written two ways -
    none of them a voltage a human would refuse to build.
    """
    directory = Path(__file__).parent / "fixtures"
    document = RequirementsDoc.model_validate(
        json.loads((directory / "live_requirements_18.json").read_text())
    )
    architecture = Architecture.model_validate(
        json.loads((directory / "live_architecture_shared_rails.json").read_text())
    )
    result = check_architecture(document, architecture, "live-rev")
    assert result.passed, [finding.model_dump() for finding in result.findings]
    assert all(finding.severity == "warning" for finding in result.findings)


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
    unknown_requirement = passing.model_copy(
        update={"tests": [passing.tests[0].model_copy(update={"source": "requirement PWR-999"})]}
    )
    assert not check_simulation(unknown_requirement, _requirements(), project_version="rev-1").passed
    assert not check_simulation(SimulationReport(tests=[]), project_version="rev-1").passed


def test_units_are_only_demanded_where_the_field_name_does_not_supply_one() -> None:
    """A deliberate loosening, and what is left of the rule after it.

    `required_ma`, `available_ma`, `margin_percent` and `measured_v` name their
    unit in the field name, so a bare number in one of them is unambiguous. The
    gate used to reject those anyway, which failed every margin test a real run
    produced. `expected` is the one free-form field, so that is where a number
    can still be meaningless - and it still is rejected there.
    """

    def rail(expected: dict[str, object], measured: object) -> SimulationReport:
        return SimulationReport(
            tests=[
                {
                    "name": "rail",
                    "expected": expected,
                    "measured_v": measured,
                    "status": "PASS",
                    "source": "onsemi NCP1117 datasheet",
                }
            ],
            assumptions=["closed form"],
        )

    def unit_findings(report: SimulationReport) -> list[str]:
        result = check_simulation(report, _requirements(), project_version="rev-1")
        return [
            str(item.actual) for item in result.findings if item.rule == "simulation values carry units"
        ]

    # Loosened: the field name carries the unit.
    assert unit_findings(rail({}, 3.3)) == []
    assert unit_findings(rail({"vout_min_v": 3.255}, "3.3 V")) == []
    # Still enforced: nothing says what 50 is.
    (flagged,) = unit_findings(rail({"ripple": 50}, "3.3 V"))
    assert "ripple" in flagged
    # A margin test's three numbers are all named for their unit.
    margins = SimulationReport(
        tests=[
            {
                "name": "rail budget",
                "required_ma": 501.45,
                "available_ma": 600.0,
                "margin_percent": 19.65,
                "status": "PASS",
                "source": "onsemi NCP1117 datasheet",
            }
        ],
        assumptions=["closed form"],
    )
    assert unit_findings(margins) == []


def test_simulation_traceability_accepts_a_selected_part_and_still_rejects_nothing() -> None:
    """Naming a part the component stage chose is citing a source.

    It is where a simulation engineer's numbers come from in this pipeline, and
    the gate used to accept only the literal word "datasheet" or an ID matching
    a hard-coded prefix list. Prose that names neither a part nor a requirement
    is still rejected, which is the case the rule exists for.
    """
    selection = ComponentSelection(
        components=[
            SelectedComponent(
                reference_group="U3",
                manufacturer_part="onsemi NCP1117ST33T3G",
                quantity=1,
                symbol="Regulator_Linear:NCP1117-3.3",
                footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2",
                reason="3.3 V LDO",
                verified_constraints=[],
                specifications=[],
            )
        ]
    )

    def gate(source: str) -> bool:
        report = SimulationReport(
            tests=[
                {
                    "name": "dropout",
                    "required_ma": 600.0,
                    "available_ma": 1000.0,
                    "margin_percent": 66.67,
                    "status": "PASS",
                    "source": source,
                }
            ],
            assumptions=["closed form"],
        )
        return check_simulation(report, _requirements(), selection, project_version="rev-1").passed

    # An ordering code cited the way an engineer writes it: a prefix of the part.
    assert gate("NCP1117 1 A output-current specification")
    assert gate("onsemi NCP1117ST33T3G limits")
    assert gate("requirement PWR-001")
    assert gate("the regulator datasheet")
    # Numbers from nowhere.
    assert not gate("worst case at 5.25 V input and 600 mA")
    assert not gate("")


def test_a_not_verified_status_is_recognised_however_it_is_spelled() -> None:
    """Real runs write "NOT_VERIFIED"; only "unverified" used to be noticed."""

    def statuses(status: str) -> list[str]:
        report = SimulationReport(
            tests=[
                {
                    "name": "thermal",
                    "required_ma": 600.0,
                    "available_ma": 600.0,
                    "margin_percent": 0.0,
                    "status": status,
                    "source": "onsemi NCP1117 datasheet",
                }
            ],
            assumptions=["closed form"],
        )
        result = check_simulation(report, _requirements(), project_version="rev-1")
        return [item.rule for item in result.findings]

    assert statuses("NOT_VERIFIED") == ["simulation model available"]
    assert statuses("unverified; no model") == ["simulation model available"]
    assert statuses("PASS") == []


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


def _drc_clean() -> ErcReport:
    """A fresh, passing DRC run, so a release test can be about hashes again."""
    return ErcReport(ran=True, errors=0, warnings=0, violations=[])


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
    assert check_qa_release(release, [file], "rev-1", drc=_drc_clean(), drc_fresh=True).passed
    assert not check_qa_release(
        release.model_copy(update={"release_hash": "bad"}), [file], "rev-1",
        drc=_drc_clean(), drc_fresh=True,
    ).passed


def test_fallbacks_do_not_invent_requirements_or_design_values() -> None:
    project = ProjectSpec(project_id="empty", current_stage="requirements", request="")
    task = AgentTask(task_id="r", assigned_agent="requirements", objective="parse")
    output = requirements_fallback(project, task, {})
    assert output.requirements == []
    assert output.power.logic_voltage == ""
    assert output.mechanical.maximum_width_mm is None
    assert output.mechanical.layers is None
    assert "USB" not in output.power.input.upper()


def _live(name: str) -> dict:
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


def test_captured_live_simulation_is_not_rejected_for_naming_its_units_in_field_names() -> None:
    """The report a real run produced, which the gate rejected fifteen times.

    Seven were margin tests whose numbers live in `required_ma`, `available_ma`
    and `margin_percent`; eight were tests that cited a selected part or a
    provided requirement. The stage was not even given the requirements it was
    told to cite. What survives is one rule, on the three tests that really do
    name no source - fixable by naming the part or quoting the requirement ID,
    which this stage can now see.
    """
    report = SimulationReport.model_validate(_live("live_simulation_margins.json"))
    requirements = RequirementsDoc.model_validate(_live("live_requirements_18.json"))
    selection = ComponentSelection.model_validate(_live("live_components_symbol_swap.json"))
    result = check_simulation(report, requirements, selection, project_version="live-rev")
    errors = [finding for finding in result.findings if finding.severity == "error"]
    assert {finding.rule for finding in errors} == {"simulation range traceability"}
    assert len(errors) == 3
    # Without the component selection the same document loses its provenance:
    # the gate has teeth, it was just being shown the wrong evidence.
    blind = check_simulation(report, requirements, project_version="live-rev")
    assert len([item for item in blind.findings if item.severity == "error"]) > len(errors)


def test_a_net_that_reaches_a_pad_counts_as_connected() -> None:
    """Pads are children of footprints, and the gate asked the board for pads.

    `find_all` only looks at direct children, so it found none, and every net
    the layout stage called critical was reported absent - on a board whose pads
    carry that very net. All five nets of a real run failed this way.
    """
    board = FIXTURES / "esp32_i2c_board" / "esp32_i2c_board.kicad_pcb"
    critical = tuple(_live("live_layout_critical_nets.json")["critical_nets"])
    result = check_layout(board, "generic_two_layer", critical, project_version="live-rev")
    assert result.passed, [finding.model_dump() for finding in result.findings]
    # A net nothing on the board declares is still missing.
    missing = check_layout(board, "generic_two_layer", ("I2C_SDA",), project_version="live-rev")
    assert not missing.passed
    assert [finding.rule for finding in missing.findings] == ["required net connected"]


def test_an_honest_checklist_failure_holds_the_release_instead_of_breaking_it(tmp_path: Path) -> None:
    """A false checklist item is a reason to hold, not a malformed document.

    Every item true used to be required outright, so a run whose DRC really had
    failed could only get past the gate by attesting that it had passed. What is
    checked now is the decision: all true is the condition for approving, and a
    complete key set is required either way.
    """
    file = tmp_path / "board.kicad_pcb"
    file.write_text("board", encoding="utf-8")
    complete = {key: True for key in RELEASE_CHECKLIST_ITEMS}
    approved = ReleaseRecord(
        release_status="approved",
        project_version="demo",
        included_files=[str(file)],
        open_critical_findings=0,
        release_hash=release_hash([file]),
        checklist=complete,
    )
    assert check_qa_release(approved, [file], "rev-1", drc=_drc_clean(), drc_fresh=True).passed

    failed_drc = {**complete, "drc_passes": False}
    held = approved.model_copy(
        update={"release_status": "needs_human_review", "checklist": failed_drc}
    )
    assert check_qa_release(held, [file], "rev-1", drc=_drc_clean(), drc_fresh=True).passed

    # The same checklist may not be called approved.
    assert not check_qa_release(
        approved.model_copy(update={"checklist": failed_drc}), [file], "rev-1",
        drc=_drc_clean(), drc_fresh=True,
    ).passed
    # And an item left out is still a malformed record.
    short = {key: value for key, value in failed_drc.items() if key != "bom_complete"}
    assert not check_qa_release(
        held.model_copy(update={"checklist": short}), [file], "rev-1",
        drc=_drc_clean(), drc_fresh=True,
    ).passed


def test_the_release_hash_is_handed_over_rather_than_guessed(tmp_path: Path) -> None:
    """An agent cannot compute a SHA-256, so the gate could never be satisfied.

    The manifest now travels on the task and the fallback hashes exactly those
    files. The gate still recomputes from disk, so a hash that no longer matches
    the files is still rejected.
    """
    for name in ("demo.kicad_sch", "demo.kicad_pcb", "notes.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    files = release_files(tmp_path)
    assert [path.name for path in files] == ["demo.kicad_pcb", "demo.kicad_sch"]

    manifest = {"included_files": [str(path) for path in files], "release_hash": release_hash(files)}
    release = fallback_for(
        "qa_release",
        ProjectSpec(project_id="demo", current_stage="qa"),
        AgentTask(
            task_id="qa",
            assigned_agent="qa_release",
            objective="release",
            release_manifest=manifest,
        ),
        {},
    ).model_copy(update={"checklist": {key: True for key in RELEASE_CHECKLIST_ITEMS}})
    assert release.release_hash == manifest["release_hash"]
    assert check_qa_release(release, files, "rev-1", drc=_drc_clean(), drc_fresh=True).passed

    (tmp_path / "demo.kicad_pcb").write_text("edited", encoding="utf-8")
    assert not check_qa_release(
        release, release_files(tmp_path), "rev-1", drc=_drc_clean(), drc_fresh=True
    ).passed


def test_the_offline_release_fallback_reports_a_complete_checklist_it_cannot_attest_to(
    tmp_path: Path,
) -> None:
    """No judgement is available offline, so every item is false and it holds.

    Reporting no checklist at all instead failed the gate's completeness rule,
    which ended every offline run on a finding about the shape of the record
    rather than about the board. A held release is now a well-formed one.
    """
    board = tmp_path / "demo.kicad_pcb"
    board.write_text("board", encoding="utf-8")
    files = release_files(tmp_path)
    release = fallback_for(
        "qa_release",
        ProjectSpec(project_id="demo", current_stage="qa"),
        AgentTask(
            task_id="qa",
            assigned_agent="qa_release",
            objective="release",
            release_manifest={
                "included_files": [str(path) for path in files],
                "release_hash": release_hash(files),
            },
        ),
        {},
    )
    assert set(release.checklist or {}) == set(RELEASE_CHECKLIST_ITEMS)
    assert not any((release.checklist or {}).values())
    assert release.release_status != "approved"
    result = check_qa_release(release, files, "rev-1")
    assert result.passed, [finding.model_dump() for finding in result.findings]

    # A release with no files is still not a release.
    empty = release.model_copy(update={"included_files": [], "release_hash": ""})
    assert not check_qa_release(empty, [], "rev-1").passed


def test_an_erc_finding_names_the_net_the_violation_is_on() -> None:
    """KiCAD says which rule broke, not where, and a stage cannot act on that.

    A real run was handed "Pins of type Power output and Power output are
    connected" with no net named, could not tell which of its ten intents caused
    it, and its repair returned the same document and failed the same way.
    """
    _, state = _project()
    conflict = Violation(
        severity="error",
        type="power_output_conflict",
        description="Pins of type Power output and Power output are connected",
        items=["J1.1", "#FLG01.1"],
        nets=["VBUS"],
    )
    result = check_schematic(
        state,
        state,
        ErcReport(ran=True, violations=[conflict]),
        erc_baseline=ErcReport(ran=True),
        project_version="rev-1",
    )
    (found,) = [item for item in result.findings if item.rule == "no new ERC violations"]
    assert "VBUS" in str(found.actual)
    assert "Power output" in str(found.actual)

    # A violation that names nothing still reports what KiCAD said.
    bare = conflict.model_copy(update={"items": [], "nets": []})
    plain = check_schematic(
        state,
        state,
        ErcReport(ran=True, violations=[bare]),
        erc_baseline=ErcReport(ran=True),
        project_version="rev-1",
    )
    (only,) = [item for item in plain.findings if item.rule == "no new ERC violations"]
    assert str(only.actual) == bare.description


def test_edge_clearance_holds_the_board_to_the_stricter_of_two_rules(tmp_path: Path) -> None:
    """The profile says what the fab can make; the board says what DRC rejects.

    They are different numbers. Naming only the profile's 0.3 mm told a real run
    to aim at 0.3 on a board whose own rule is 0.5, so its placement satisfied
    the figure it was given and the DRC that ran immediately after failed it -
    four times, until the return trips ran out.
    """
    source = FIXTURES / "esp32_i2c_board" / "esp32_i2c_board.kicad_pcb"
    board = tmp_path / "board.kicad_pcb"
    board.write_text(source.read_text(), encoding="utf-8")
    assert copper_edge_clearance(load(board)) == 0.5

    result = check_layout(board, "generic_two_layer", project_version="rev-1")
    expectations = " ".join(str(item.expected) for item in result.findings)
    if result.findings:
        # Whatever it reports, the figure it names is the one DRC will enforce.
        assert "0.5" in expectations

    # A board that declares a looser rule than the profile is still held to the
    # profile: the greater of the two, not whichever was asked for last.
    relaxed = board.read_text(encoding="utf-8").replace(
        "(setup", "(setup\n\t\t(rules (min_copper_edge_clearance 0.1))", 1
    )
    board.write_text(relaxed, encoding="utf-8")
    assert copper_edge_clearance(load(board)) == 0.1
    loose = check_layout(board, "generic_two_layer", project_version="rev-1")
    if loose.findings:
        assert "0.3" in " ".join(str(item.expected) for item in loose.findings)


def test_a_verification_finding_is_told_which_field_is_empty() -> None:
    """`evidence` and `evidence_source` are different fields on the same finding.

    The rule that policed the first was named after the second, so a real run -
    which had filled `evidence_source` correctly - was told that field was wrong,
    returned the same document, and its repair failed identically.
    """
    report = VerificationReport(
        requirements_total=1,
        requirements_passed=0,
        requirements_failed=1,
        requirements_unverified=0,
        critical_findings=[
            VerificationFinding(
                requirement_id="PWR-001",
                finding="rail unsourced",
                evidence={},
                rule="rail has a source",
                actual="none",
                expected="a regulator",
                kicad_object="+3V3",
                evidence_source="kicad-cli ERC",
                severity="error",
            )
        ],
        decision="reject",
    )
    result = check_verification(report, "rev-1")
    rules = [item.rule for item in result.findings]
    # The finding is severity "error", so the stage is blocked for that too; what
    # this test is about is that the empty field is named as the field it is.
    assert "verification finding has evidence" in rules
    assert "evidence_source" not in "".join(
        rule for rule in rules if rule == "verification finding has evidence"
    )

    populated = report.model_copy(
        update={
            "critical_findings": [
                report.critical_findings[0].model_copy(
                    update={"evidence": {"tool": "kicad-cli ERC", "net": "+3V3"}}
                )
            ]
        }
    )
    # Filling `evidence` settles the completeness complaint - but the finding is
    # still a critical one, and a stage that reports one no longer passes.
    populated_rules = [item.rule for item in check_verification(populated, "rev-1").findings]
    assert "verification finding has evidence" not in populated_rules
    assert populated_rules == ["verification reports no unresolved critical finding"]

    # The same finding at an advisory severity is a complete document and passes.
    advisory = populated.model_copy(
        update={
            "critical_findings": [
                populated.critical_findings[0].model_copy(update={"severity": "warning"})
            ],
            "requirements_failed": 0,
            "requirements_passed": 1,
        }
    )
    assert check_verification(advisory, "rev-1").passed

    # A field left blank is still named, and named as itself.
    blank = populated.model_copy(
        update={
            "critical_findings": [
                populated.critical_findings[0].model_copy(update={"kicad_object": ""})
            ]
        }
    )
    incomplete = check_verification(blank, "rev-1")
    assert not incomplete.passed
    completeness = [
        item for item in incomplete.findings if item.rule == "verification finding completeness"
    ]
    assert "kicad_object" in str(completeness[0].actual)


def test_an_edge_clearance_finding_says_which_way_to_move_and_how_far() -> None:
    """A negative clearance is not something a placement can act on.

    A real run was told "J1 clears the outline by -2 mm", moved J1 the wrong
    way, was told -5 mm, moved it further still, and used up its return trips
    going in the wrong direction. Which edge is crossed and which way the centre
    has to go are the parts that make the finding fixable.
    """
    outline = (35.0, 35.0, 145.0, 100.0)

    def advise(extent: tuple[float, float, float, float]) -> str | None:
        result = _clearance_advice(extent, outline, 0.5)
        return None if result is None else result[1]

    assert advise((33.0, 61.5, 47.0, 73.5)) == "increase x by at least 2.5 mm to clear the left edge"
    assert advise((140.0, 61.5, 154.0, 73.5)) == "decrease x by at least 9.5 mm to clear the right edge"
    assert advise((43.0, 30.0, 57.0, 44.0)) == "increase y by at least 5.5 mm to clear the top edge"
    # Two edges at once is a corner, and both moves are named.
    corner = advise((33.0, 33.0, 47.0, 47.0))
    assert corner is not None and "left edge" in corner and "top edge" in corner
    # A footprint that cannot fit at any position says so instead of a move.
    assert advise((30.0, 61.5, 150.0, 73.5)) == "the footprint is wider than the outline allows at any x"
    # And a placement with room to spare is not a finding at all.
    assert advise((43.0, 61.5, 57.0, 73.5)) is None


def test_verification_does_not_pass_while_it_still_names_a_critical_finding() -> None:
    """The stage's own verdict, enforced.

    Verification exists to say whether the design meets its requirements. A
    report that names an unresolved critical finding has said it does not, and
    the gate used to wave it through as long as the finding was well formed.
    """
    finding = VerificationFinding(
        requirement_id="PWR-001",
        finding="+3V3 has no source",
        evidence={"tool": "kicad-cli ERC", "net": "+3V3"},
        rule="rail has a source",
        actual="none",
        expected="a regulator",
        kicad_object="+3V3",
        evidence_source="kicad-cli ERC",
        severity="critical",
    )
    report = VerificationReport(
        requirements_total=1,
        requirements_passed=0,
        requirements_failed=1,
        requirements_unverified=0,
        critical_findings=[finding],
        decision="reject",
    )
    result = check_verification(report, "rev-1")
    assert not result.passed
    assert "verification reports no unresolved critical finding" in [
        item.rule for item in result.findings
    ]

    # `high` is not a softer word for the same thing: the live stages used it for
    # real blockers ("FAIL. The 500 mA continuous 3.3 V budget is unmet twice
    # over"), so it blocks too.
    assert not check_verification(
        report.model_copy(update={"critical_findings": [finding.model_copy(update={"severity": "high"})]}),
        "rev-1",
    ).passed

    # Advisory severities are what the stage reports when nothing is blocking.
    for severity in ("info", "low", "medium", "warning"):
        advisory = report.model_copy(
            update={
                "critical_findings": [finding.model_copy(update={"severity": severity})],
                "requirements_failed": 0,
                "requirements_passed": 1,
            }
        )
        assert check_verification(advisory, "rev-1").passed, severity


def test_an_unrecognised_severity_blocks_rather_than_being_assumed_harmless() -> None:
    """`severity` is a free string, so the benign words are the allowlist.

    Six different words have already appeared in this field across live runs. A
    blocklist of the three that happened to mean "blocking" would let a seventh
    nobody anticipated walk through the one gate meant to stop it.
    """
    report = VerificationReport(
        requirements_total=1,
        requirements_passed=1,
        requirements_failed=0,
        requirements_unverified=0,
        critical_findings=[
            VerificationFinding(
                requirement_id="PWR-001",
                finding="rail unsourced",
                evidence={"tool": "kicad-cli ERC"},
                rule="rail has a source",
                actual="none",
                expected="a regulator",
                kicad_object="+3V3",
                evidence_source="kicad-cli ERC",
                severity="P0",
            )
        ],
        decision="reject",
    )
    assert not check_verification(report, "rev-1").passed


def test_deleting_the_findings_does_not_get_a_failed_requirement_past_the_gate() -> None:
    """The exit a live repair actually took, closed.

    Run 3db7318c5d59's repair turned nine critical findings into zero and left
    `requirements_failed` at 1 and the decision at "reject" - a document that
    contradicts itself, which the gate then certified. Making critical findings
    fail the gate makes deleting them the cheapest way past it, so the two rules
    only work as a pair.
    """
    emptied = VerificationReport(
        requirements_total=1,
        requirements_passed=0,
        requirements_failed=1,
        requirements_unverified=0,
        critical_findings=[],
        decision="reject",
    )
    result = check_verification(emptied, "rev-1")
    assert not result.passed
    assert "a failing verification is explained by a blocking finding" in [
        item.rule for item in result.findings
    ]

    # The per-requirement outcomes say the same thing on their own.
    by_outcome = VerificationReport(
        requirements_total=1,
        requirements_passed=0,
        requirements_failed=0,
        requirements_unverified=0,
        critical_findings=[],
        decision="reject",
        requirement_outcomes=[{"requirement_id": "PWR-001", "status": "failed", "evidence": {"a": 1}}],
    )
    assert not check_verification(by_outcome, "rev-1").passed

    # A report with nothing failing is a clean pass, not a document with a hole.
    clean = emptied.model_copy(
        update={"requirements_failed": 0, "requirements_passed": 1, "decision": "accept"}
    )
    assert check_verification(clean, "rev-1").passed


def test_approval_is_caught_however_the_stage_spells_it(tmp_path: Path) -> None:
    """`release_status` is a free string the stage writes, compared exactly before.

    So `Approved` and `release_approved` were not approvals as far as the gate
    was concerned, and walked past the checklist requirement they exist to meet.
    """
    file = tmp_path / "board.kicad_pcb"
    file.write_text("board", encoding="utf-8")
    checklist = {key: key != "drc_passes" for key in RELEASE_CHECKLIST_ITEMS}
    record = ReleaseRecord(
        release_status="needs_human_review",
        project_version="demo",
        included_files=[str(file)],
        open_critical_findings=0,
        release_hash=release_hash([file]),
        checklist=checklist,
    )
    # Holding on an unmet item is legal, whatever else is true.
    assert check_qa_release(record, [file], "rev-1").passed

    for spelling in ("approved", "Approved", "APPROVED", "release_approved", " approved "):
        claimed = record.model_copy(update={"release_status": spelling})
        assert not check_qa_release(claimed, [file], "rev-1").passed, spelling


def test_drc_passes_is_checked_against_the_drc_report_not_the_record(tmp_path: Path) -> None:
    """The one checklist item with something outside the record to check it against.

    Every other item is an attestation the stage writes about itself. `drc_passes`
    has the layout stage's DRC report behind it, and a release that claims a
    clean DRC over a board that failed one is exactly the case the gate exists
    to refuse.
    """
    file = tmp_path / "board.kicad_pcb"
    file.write_text("board", encoding="utf-8")
    record = ReleaseRecord(
        release_status="needs_human_review",
        project_version="demo",
        included_files=[str(file)],
        open_critical_findings=0,
        release_hash=release_hash([file]),
        checklist={key: True for key in RELEASE_CHECKLIST_ITEMS},
    )
    failing = ErcReport(ran=True, errors=6, warnings=14, violations=[])
    passing = ErcReport(ran=True, errors=0, warnings=0, violations=[])

    assert check_qa_release(record, [file], "rev-1", drc=passing, drc_fresh=True).passed

    rejected = check_qa_release(record, [file], "rev-1", drc=failing, drc_fresh=True)
    assert not rejected.passed
    assert "drc_passes agrees with the DRC report" in [item.rule for item in rejected.findings]

    # A baseline from before the layout stage describes a different board.
    assert not check_qa_release(record, [file], "rev-1", drc=passing, drc_fresh=False).passed
    # And no DRC at all is not evidence of a passing one.
    assert not check_qa_release(record, [file], "rev-1").passed

    # Under-claiming is always fine: the item is false, so nothing is attested.
    honest = record.model_copy(
        update={"checklist": {key: key != "drc_passes" for key in RELEASE_CHECKLIST_ITEMS}}
    )
    assert check_qa_release(honest, [file], "rev-1", drc=failing, drc_fresh=True).passed


def test_relabelling_a_finding_info_is_the_same_escape_as_deleting_it() -> None:
    """Deleting a finding and calling it advisory are one move, made two ways.

    Anchoring the rule on `critical_findings == []` closed only the first: a
    report could keep a failed requirement, keep a fully populated finding, drop
    its severity to "info", and pass a gate whose whole job is to stop exactly
    that. The rule is anchored on what blocks instead.
    """
    finding = VerificationFinding(
        requirement_id="PWR-001",
        finding="+3V3 has no source",
        evidence={"tool": "kicad-cli ERC", "net": "+3V3"},
        rule="rail has a source",
        actual="none",
        expected="a regulator",
        kicad_object="+3V3",
        evidence_source="kicad-cli ERC",
        severity="info",
    )
    downgraded = VerificationReport(
        requirements_total=1,
        requirements_passed=0,
        requirements_failed=1,
        requirements_unverified=0,
        critical_findings=[finding],
        decision="reject",
    )
    result = check_verification(downgraded, "rev-1")
    assert not result.passed
    assert "a failing verification is explained by a blocking finding" in [
        item.rule for item in result.findings
    ]

    # The same through requirement_outcomes, with the counters left at zero.
    # Built rather than copied: model_copy does not validate, so the outcomes
    # would stay plain dicts and never reach the rule under test.
    by_outcome = VerificationReport(
        requirements_total=1,
        requirements_passed=0,
        requirements_failed=0,
        requirements_unverified=0,
        critical_findings=[finding],
        decision="reject",
        requirement_outcomes=[
            {"requirement_id": "PWR-001", "status": "failed", "evidence": {"tool": "erc"}}
        ],
    )
    assert not check_verification(by_outcome, "rev-1").passed

    # Nothing failing and an advisory finding is a clean, complete document.
    clean = downgraded.model_copy(update={"requirements_failed": 0, "requirements_passed": 1})
    assert check_verification(clean, "rev-1").passed


def test_a_record_saying_it_is_not_approved_is_not_read_as_an_approval(tmp_path: Path) -> None:
    """The obvious fix for the exact-match bug is a substring test, and it is wrong.

    `"approv" in status` calls `not_approved`, `unapproved`, `pending_approval`
    and `approval_withheld` approvals too - so a record honestly holding gets
    rejected for claiming something it explicitly denied, which then costs a
    repair attempt and someone's attention on a non-problem.
    """
    file = tmp_path / "board.kicad_pcb"
    file.write_text("board", encoding="utf-8")
    record = ReleaseRecord(
        release_status="needs_human_review",
        project_version="demo",
        included_files=[str(file)],
        open_critical_findings=0,
        release_hash=release_hash([file]),
        # One item unmet, so anything read as an approval is rejected.
        checklist={key: key != "drc_passes" for key in RELEASE_CHECKLIST_ITEMS},
    )
    for honest in (
        "needs_human_review",
        "not_approved",
        "not approved",
        "unapproved",
        "pending_approval",
        "approval_withheld",
        "approval denied",
        "rejected",
    ):
        assert check_qa_release(
            record.model_copy(update={"release_status": honest}), [file], "rev-1"
        ).passed, honest

    for claimed in ("approved", "Approved", "release-approved", "approve"):
        assert not check_qa_release(
            record.model_copy(update={"release_status": claimed}), [file], "rev-1"
        ).passed, claimed
