"""Offline workflow orchestration tests."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings
from app.kicad.erc import KicadCli
from app.kicad.reader import read_project
from app.models import ErcReport
from app.team.cli import _print_report
from app.team.fallbacks import qa_release_fallback
from app.team.orchestrator import OrchestratorOptions, TeamOrchestrator
from app.team.profiles import get_profile
from app.team.runner import StubAgentRunner
from app.team.schemas import (
    AgentResult,
    AgentTask,
    Architecture,
    CheckFinding,
    ComponentSelection,
    LayoutProposal,
    ManufacturingReport,
    PMPlan,
    ProjectSpec,
    RequirementsDoc,
    SchematicIntents,
    SimulationReport,
    StageCheckResult,
    VerificationReport,
)

FIXTURE = Path(__file__).parents[2] / "fixtures" / "projects" / "esp32_i2c_board"


def _project(tmp_path: Path) -> tuple[ProjectSpec, Path]:
    directory = tmp_path / "project"
    shutil.copytree(FIXTURE, directory)
    project = ProjectSpec(
        project_id="demo",
        request="Use I2C at 3.3V",
        current_stage="team",
        manufacturer_profile={"name": "generic_two_layer"},
        design_context=None,
    )
    return project, directory


def _outputs(project: ProjectSpec, directory: Path) -> dict[str, object]:
    requirements = RequirementsDoc(
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
    profile = get_profile("generic_two_layer")
    files = [str(path) for path in directory.glob("*.kicad_*")]
    release = qa_release_fallback(
        project,
        AgentTask(task_id="qa", assigned_agent="qa_release", objective="release"),
        {
            "project_files": files,
            "release_checklist": {
                key: True
                for key in (
                    "all_agents_completed",
                    "no_open_critical_findings",
                    "erc_passes",
                    "drc_passes",
                    "simulation_tests_pass",
                    "requirements_traceable",
                    "bom_complete",
                    "symbols_match_footprints",
                    "fabrication_files_generated",
                    "files_match_project_version",
                    "no_unexpected_release_changes",
                )
            },
        },
    )
    return {
        "project_manager": PMPlan(
            project_goal="demo",
            workflow=[
                "requirements",
                "architecture",
                "components",
                "schematic_design",
                "pcb_layout",
                "simulation",
                "verification",
                "manufacturing",
                "qa_release",
            ],
            status="ready",
        ),
        "requirements": requirements,
        "architecture": Architecture(
            blocks=[{"id": "power", "type": "power", "requirement_ids": ["PWR-001"]}],
            connections=[],
        ),
        "components": ComponentSelection(components=[]),
        "schematic_design": SchematicIntents(
            protocol="I2C",
            logic_voltage="3.3V",
            pullup_value="",
            protected_objects=[],
            assumptions=[],
            intents=[],
        ),
        "pcb_layout": LayoutProposal(placements=[], critical_nets=[], unrouted_nets=[]),
        "simulation": SimulationReport(tests=[], models=[], assumptions=["unverified"]),
        "verification": VerificationReport(
            requirements_total=1,
            requirements_passed=1,
            requirements_failed=0,
            requirements_unverified=0,
            critical_findings=[],
            decision="passed",
        ),
        "manufacturing": ManufacturingReport(
            manufacturer_profile=profile.name,
            dfm_status="passed",
            findings=[],
            fabrication_ready=True,
            profile_rules={
                "minimum_trace_width_mm": profile.minimum_trace_width_mm,
                "minimum_spacing_mm": profile.minimum_spacing_mm,
                "minimum_drill_mm": profile.minimum_drill_mm,
                "copper_to_edge_clearance_mm": profile.copper_to_edge_clearance_mm,
                "supported_layer_count": 2.0,
            },
            profile_provenance=profile.provenance,
        ),
        "qa_release": release,
    }


def _baselines(directory: Path) -> tuple[ErcReport, ErcReport]:
    cli = KicadCli(settings.kicad_cli)
    state = read_project(directory)
    return cli.run_erc(state.schematic_path), cli.run_drc_baseline(directory / "esp32_i2c_board.kicad_pcb")


def test_stub_workflow_reaches_exact_review_wording(tmp_path: Path, monkeypatch) -> None:
    project, directory = _project(tmp_path)
    state = read_project(directory)
    erc_baseline, drc_baseline = _baselines(directory)
    project.design_context = None
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "workspace")
    runner = StubAgentRunner(outputs=_outputs(project, directory))
    report = TeamOrchestrator(
        runner,
        run_id="happy",
        workspace_dir=tmp_path / "workspace",
        options=OrchestratorOptions(
            project_dir=directory,
            board_path=directory / "esp32_i2c_board.kicad_pcb",
            project_state=state,
            erc_baseline=erc_baseline,
            drc_baseline=drc_baseline,
        ),
    ).run(project)
    assert report.release_status == "ready for engineering review"
    assert report.summary == "ready for engineering review"


def test_report_marks_requirements_not_run_before_verification(capsys, tmp_path: Path) -> None:
    project, directory = _project(tmp_path)
    requirements = _outputs(project, directory)["requirements"]
    orchestrator = TeamOrchestrator(
        StubAgentRunner(outputs={}),
        run_id="requirements-not-run",
        workspace_dir=tmp_path / "workspace",
    )
    report = orchestrator._report(
        project,
        {
            "requirements": AgentResult(
                task_id="requirements",
                agent="requirements",
                status="completed",
                output=requirements,
            )
        },
        {},
        [],
        "needs_human_review",
    )
    _print_report(report)
    assert report.requirements_status == "not run"
    assert "Requirements satisfied: not run" in capsys.readouterr().out


def test_failed_requirements_gate_routes_back_until_human_review(tmp_path: Path) -> None:
    project, _ = _project(tmp_path)
    outputs = _outputs(project, tmp_path / "project")
    outputs["requirements"] = RequirementsDoc(
        requirements=[
            {
                "id": "PWR-001",
                "category": "interface",
                "statement": "bad category",
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
    report = TeamOrchestrator(
        StubAgentRunner(outputs=outputs),
        run_id="routing",
        workspace_dir=tmp_path / "workspace",
    ).run(project)
    assert report.release_status == "needs_human_review"
    assert "requirements" in report.per_stage_results


def test_parallel_and_sequential_reports_match(tmp_path: Path, monkeypatch) -> None:
    reports = []
    for parallel in (True, False):
        project, directory = _project(tmp_path / ("parallel" if parallel else "sequential"))
        state = read_project(directory)
        erc_baseline, drc_baseline = _baselines(directory)
        monkeypatch.setattr(settings, "team_parallel", parallel)
        reports.append(
            TeamOrchestrator(
                StubAgentRunner(outputs=_outputs(project, directory)),
                run_id="same-run",
                workspace_dir=tmp_path / ("workspace-" + str(parallel)),
                options=OrchestratorOptions(
                    project_dir=directory,
                    board_path=directory / "esp32_i2c_board.kicad_pcb",
                    project_state=state,
                    erc_baseline=erc_baseline,
                    drc_baseline=drc_baseline,
                ),
            ).run(project)
        )

    def normalize_paths(value):
        if isinstance(value, dict):
            return {key: normalize_paths(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize_paths(item) for item in value]
        if isinstance(value, str):
            return value.replace(str(tmp_path / "parallel"), "<project>").replace(
                str(tmp_path / "sequential"), "<project>"
            )
        return value

    assert normalize_paths(reports[0].model_dump(mode="json")) == normalize_paths(
        reports[1].model_dump(mode="json")
    )


def test_read_only_mutation_fails_the_run(tmp_path: Path, monkeypatch) -> None:
    project, directory = _project(tmp_path)
    state = read_project(directory)
    outputs = _outputs(project, directory)
    erc_baseline, drc_baseline = _baselines(directory)
    outputs["verification"] = LayoutProposal(placements=[], critical_nets=[], unrouted_nets=[])
    monkeypatch.setattr(settings, "team_parallel", False)
    report = TeamOrchestrator(
        StubAgentRunner(outputs=outputs),
        run_id="readonly",
        workspace_dir=tmp_path / "workspace",
        options=OrchestratorOptions(
            project_dir=directory,
            board_path=directory / "esp32_i2c_board.kicad_pcb",
            project_state=state,
            erc_baseline=erc_baseline,
            drc_baseline=drc_baseline,
        ),
    ).run(project)
    assert report.release_status == "failed"


def test_missing_kicad_cli_is_not_run_and_needs_human_review(tmp_path: Path) -> None:
    project, directory = _project(tmp_path)
    state = read_project(directory)

    class MissingCli:
        def run_erc(self, _path: Path) -> ErcReport:
            return ErcReport(ran=False, raw_output="kicad-cli not found")

        def run_drc(self, _path: Path) -> ErcReport:
            return ErcReport(ran=False, raw_output="kicad-cli not found")

    report = TeamOrchestrator(
        StubAgentRunner(outputs=_outputs(project, directory)),
        run_id="missing-cli",
        workspace_dir=tmp_path / "workspace",
        options=OrchestratorOptions(
            project_dir=directory,
            board_path=directory / "esp32_i2c_board.kicad_pcb",
            project_state=state,
            erc_baseline=ErcReport(ran=True),
            kicad_cli=MissingCli(),  # type: ignore[arg-type]
        ),
    ).run(project)
    assert report.release_status == "needs_human_review"
    assert report.erc_status == "not run"


def test_failure_routing_follows_manufacturing_and_release_rules() -> None:
    def gate(rule: str, stage: str = "manufacturing") -> StageCheckResult:
        return StageCheckResult(
            stage=stage,
            passed=False,
            findings=[
                CheckFinding(
                    rule=rule,
                    actual="bad",
                    expected="good",
                    kicad_object="test",
                    evidence_source="test",
                    severity="error",
                )
            ],
        )

    assert TeamOrchestrator._route("manufacturing", gate("trace width")) == "pcb_layout"
    assert TeamOrchestrator._route("manufacturing", gate("minimum drill")) == "pcb_layout"
    assert TeamOrchestrator._route("manufacturing", gate("footprint plausibility")) == "components"
    assert TeamOrchestrator._route("manufacturing", gate("manufacturer profile available")) == "manufacturing"
    assert (
        TeamOrchestrator._route("qa_release", gate("release checklist complete", "qa_release"))
        == "qa_release"
    )


def test_schematic_gate_uses_fresh_erc_after_mutation(tmp_path: Path) -> None:
    project, directory = _project(tmp_path)
    state = read_project(directory)
    calls = 0

    class FreshCli:
        def run_erc(self, _path: Path) -> ErcReport:
            nonlocal calls
            calls += 1
            return ErcReport(
                ran=True,
                violations=[
                    {
                        "severity": "error",
                        "type": "connection",
                        "description": "new bad connection",
                    }
                ],
            )

        def run_drc(self, _path: Path) -> ErcReport:
            return ErcReport(ran=True)

    def mutate(output: SchematicIntents, project_dir: Path) -> None:
        del output
        schematic = next(project_dir.glob("*.kicad_sch"))
        schematic.write_text(schematic.read_text() + "\n")

    report = TeamOrchestrator(
        StubAgentRunner(outputs=_outputs(project, directory)),
        run_id="fresh-erc",
        workspace_dir=tmp_path / "workspace",
        options=OrchestratorOptions(
            project_dir=directory,
            board_path=directory / "esp32_i2c_board.kicad_pcb",
            project_state=state,
            erc_baseline=ErcReport(ran=True),
            kicad_cli=FreshCli(),  # type: ignore[arg-type]
            schematic_applier=mutate,
        ),
    ).run(project)
    assert calls == 3
    assert report.release_status == "needs_human_review"
    assert report.erc_status == "failed"
