"""Command-line entry point for deterministic team runs."""

from __future__ import annotations

import argparse
import shutil
import uuid
from pathlib import Path

from ..config import settings
from ..kicad.erc import KicadCli
from ..kicad.reader import read_project
from .evidence import EvidenceStore
from .orchestrator import OrchestratorOptions, TeamOrchestrator
from .runner import DevinAgentRunner, StubAgentRunner
from .schemas import DesignContext, ProjectSpec, TeamRunReport
from .schematic import apply_schematic_intents


def _project_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    candidate = settings.projects_dir / value
    if candidate.is_dir():
        return candidate.resolve()
    candidate = settings.projects_dir / Path(value).name
    if candidate.is_dir():
        return candidate.resolve()
    raise SystemExit(f"project not found: {value}")


def run_cli(project_value: str, request: str, runner_name: str | None = None) -> int:
    source = _project_path(project_value)
    run_id = f"cli-{uuid.uuid4().hex[:10]}"
    workspace = settings.workspace_dir
    project_dir = workspace / "team" / run_id / "project"
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, project_dir)

    cli = KicadCli(settings.kicad_cli)
    state = read_project(project_dir)
    erc = cli.run_erc(state.schematic_path)
    board = next(iter(project_dir.glob("*.kicad_pcb")), None)
    drc = cli.run_drc_baseline(board) if board else None
    project = ProjectSpec(
        project_id=project_dir.name,
        request=request,
        current_stage="team",
        manufacturer_profile={"name": settings.team_manufacturer_profile},
        design_context=DesignContext.from_project(state, erc, drc, board),
    )
    evidence = EvidenceStore(run_id)
    runner_name = runner_name or settings.resolved_team_runner
    if runner_name not in {"devin", "stub"}:
        raise SystemExit("runner must be 'devin' or 'stub'")
    runner_class = {"devin": DevinAgentRunner, "stub": StubAgentRunner}[runner_name]
    runner = runner_class(evidence=evidence)
    runner_detail = {
        "devin": "live Devin sessions",
        "stub": "offline deterministic; no Devin sessions",
    }[runner_name]
    print(f"RUNNER: {runner_name.upper()} ({runner_detail})")
    report = TeamOrchestrator(
        runner,
        run_id=run_id,
        workspace_dir=workspace,
        options=OrchestratorOptions(
            project_dir=project_dir,
            board_path=board,
            project_state=state,
            erc_baseline=erc,
            drc_baseline=drc,
            kicad_cli=cli,
            schematic_applier=apply_schematic_intents,
        ),
    ).run(project)
    _print_report(report)
    return 0


def _print_report(report: TeamRunReport) -> None:
    if report.requirements_status == "not run":
        print("Requirements satisfied: not run")
    else:
        print(f"Requirements satisfied: {report.requirements_satisfied}/{report.requirements_total}")
    print(f"Schematic ERC: {report.erc_status}")
    print(f"PCB DRC: {report.drc_status}")
    if report.power_tests_status == "not run":
        print("Power tests: not run")
    else:
        print(f"Power tests: {report.power_tests_passed}/{report.power_tests_total}")
    if report.manufacturing_status == "not run":
        print("Manufacturing checks: not run")
    else:
        print(f"Manufacturing checks: {report.manufacturing_warnings} warnings")
    print(f"Critical issues: {report.critical_issues}")
    if report.release_status == "ready for engineering review":
        print("Release status: Ready for engineering review")
    else:
        print(f"Release status: {report.release_status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the KiCAD Mitos engineering team")
    parser.add_argument("--project", required=True)
    parser.add_argument("--runner", choices=("devin", "stub"), default=settings.resolved_team_runner)
    parser.add_argument("request")
    args = parser.parse_args()
    raise SystemExit(run_cli(args.project, args.request, args.runner))


if __name__ == "__main__":
    main()
