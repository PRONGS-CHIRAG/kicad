#!/usr/bin/env python3
"""Replay a captured team run's stage outputs through the real orchestrator.

A live run spawns ten Devin sessions and takes about half an hour, so diagnosing
a stage gate by re-running it is slow and expensive. Every run already writes the
documents its agents produced to `$KICAD_MITOS_WORKSPACE/team/<run_id>/`, and a
run that died partway still left everything it got as far as. This feeds those
documents back through `TeamOrchestrator` with `StubAgentRunner`, so the gates,
the routing and the repair stage all run for real against real agent output and
nothing is spent.

Two things to know about what a replay can and cannot show:

  - Each stage is pinned to one fixed document, so a stage whose gate rejects it
    hands back the identical answer and the run stops at the return-trip cap. A
    replay tells you which gates reject real output, not whether a live run would
    converge.
  - A stage with no captured output falls through to its fallback, which is the
    only way to exercise stages a run never reached.

    scripts/replay_team_run.py <run_id> [--project esp32_i2c_existing_pullups]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.kicad.erc import KicadCli  # noqa: E402
from app.kicad.reader import read_project  # noqa: E402
from app.team.orchestrator import CANONICAL_ORDER, OrchestratorOptions, TeamOrchestrator  # noqa: E402
from app.team.profiles import get_profile  # noqa: E402
from app.team.registry import output_models  # noqa: E402
from app.team.runner import StubAgentRunner  # noqa: E402
from app.team.schemas import DesignContext, ProjectSpec  # noqa: E402


def _load_outputs(run_dir: Path) -> dict[str, object]:
    """Every stage document the captured run managed to produce."""
    loaded: dict[str, object] = {}
    for stage, model in output_models().items():
        path = run_dir / f"{stage}.json"
        if path.is_file():
            loaded[stage] = model.model_validate(json.loads(path.read_text()))
    return loaded


def _working_copy(project: str) -> Path:
    """A throwaway copy, because the schematic and layout stages rewrite files."""
    source = ROOT / "fixtures" / "projects" / project
    if not source.is_dir():
        raise SystemExit(f"no such fixture project: {source}")
    work = Path(tempfile.mkdtemp()) / "project"
    shutil.copytree(source, work)
    for path in sorted(work.glob(f"{project}.*")):
        path.rename(path.with_name("project" + path.suffix))
    return work


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="a directory name under <workspace>/team/")
    parser.add_argument("--project", default="esp32_i2c_existing_pullups")
    parser.add_argument("--profile", default="generic_two_layer")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(tempfile.gettempdir()) / "kicad-replay",
        help="where the replay writes its own evidence; stable so it can be read afterwards",
    )
    args = parser.parse_args()

    run_dir = settings.workspace_dir / "team" / args.run_id
    if not run_dir.is_dir():
        raise SystemExit(f"no captured run at {run_dir}")

    work = _working_copy(args.project)
    cli = KicadCli(settings.kicad_cli)
    state = read_project(work)
    erc = cli.run_erc(state.schematic_path)
    board = next(iter(work.glob("*.kicad_pcb")), None)
    drc = cli.run_drc_baseline(board) if board else None

    captured = run_dir / "final-report.json"
    request = json.loads(captured.read_text()).get("request", "") if captured.is_file() else ""
    project = ProjectSpec(
        project_id="project",
        request=request,
        current_stage="team",
        manufacturer_profile=get_profile(args.profile).model_dump(mode="json"),
        design_context=DesignContext.from_project(state, erc, drc, board),
    )

    outputs = _load_outputs(run_dir)
    print(f"replaying {len(outputs)} captured stages from {run_dir}")
    report = TeamOrchestrator(
        StubAgentRunner(outputs=outputs),
        run_id="replay",
        workspace_dir=args.workspace,
        options=OrchestratorOptions(
            project_dir=work,
            board_path=board,
            project_state=state,
            erc_baseline=erc,
            drc_baseline=drc,
            kicad_cli=cli,
        ),
    ).run(project)

    evidence = args.workspace / "team" / "replay"
    print(f"\nrelease_status: {report.release_status}\n")
    for stage in CANONICAL_ORDER:
        path = evidence / f"{stage}-gate.json"
        if not path.is_file():
            print(f"  {stage:18s} not reached")
            continue
        gate = json.loads(path.read_text())
        errors = [item for item in gate.get("findings", []) if item.get("severity") == "error"]
        rules = sorted({item["rule"] for item in errors})
        source = "captured" if stage in outputs else "fallback"
        print(f"  {stage:18s} {source:9s} passed={gate.get('passed')!s:5s} errors={len(errors):2d} {rules}")
    print(f"\nevidence: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
