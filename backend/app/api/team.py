"""Background API for ten-agent team runs."""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import settings
from ..kicad.erc import KicadCli
from ..kicad.reader import read_project
from ..team.evidence import EvidenceStore
from ..team.orchestrator import OrchestratorOptions, TeamOrchestrator
from ..team.profiles import get_profile
from ..team.runner import DevinAgentRunner, StubAgentRunner
from ..team.schemas import DesignContext, ProjectSpec, TeamRunReport
from ..team.schematic import apply_schematic_intents
from ..workflow import store

router = APIRouter(prefix="/api/team")
_executor = ThreadPoolExecutor(max_workers=4)
_runs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


class TeamRunRequest(BaseModel):
    project: str
    request: str = ""
    runner: str | None = None
    manufacturer_profile: str | None = None


class TeamAnswerRequest(BaseModel):
    answer: str = Field(min_length=1)


def _get_run(run_id: str) -> dict[str, Any]:
    with _lock:
        run = _runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown team run {run_id}")
    return run


def _run_team(run_id: str, request: TeamRunRequest, project_dir: Path) -> None:
    try:
        cli = KicadCli(settings.kicad_cli)
        state = read_project(project_dir)
        erc = cli.run_erc(state.schematic_path)
        board = next(iter(project_dir.glob("*.kicad_pcb")), None)
        drc = cli.run_drc_baseline(board) if board else None
        # The whole profile, not just its name: the DFM gate compares the report
        # against these exact values and provenance, and the layout stage is held
        # to this edge clearance. An agent that is only told the profile's name
        # has to guess both, which is a gate it cannot pass by answering better.
        profile = get_profile(request.manufacturer_profile or settings.team_manufacturer_profile)
        project = ProjectSpec(
            project_id=project_dir.name,
            request=request.request,
            current_stage="team",
            manufacturer_profile=profile.model_dump(mode="json"),
            design_context=DesignContext.from_project(state, erc, drc, board),
        )
        runner_name = request.runner or settings.resolved_team_runner
        evidence = EvidenceStore(run_id)
        runner = (
            DevinAgentRunner(evidence=evidence)
            if runner_name == "devin"
            else StubAgentRunner(evidence=evidence)
        )
        orchestrator = TeamOrchestrator(
            runner,
            run_id=run_id,
            options=OrchestratorOptions(
                project_dir=project_dir,
                board_path=board,
                project_state=state,
                erc_baseline=erc,
                drc_baseline=drc,
                kicad_cli=cli,
                schematic_applier=apply_schematic_intents,
            ),
        )
        report = orchestrator.run(project)
        with _lock:
            _runs[run_id].update(status="completed", report=report, runner=runner_name)
    except Exception as exc:
        with _lock:
            _runs[run_id].update(status="failed", error=str(exc))


@router.post("/runs", status_code=202)
def create_team_run(request: TeamRunRequest) -> dict[str, str]:
    try:
        session = store.create(request.project)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    run_id = uuid.uuid4().hex[:12]
    with _lock:
        _runs[run_id] = {
            "run_id": run_id,
            "status": "running",
            "runner": request.runner or settings.resolved_team_runner,
            "project": request.project,
            "request": request.request,
            "manufacturer_profile": request.manufacturer_profile or settings.team_manufacturer_profile,
            "answers": [],
            # The team edits this session's working copy, so the render endpoint
            # can show the very files the schematic and layout stages write.
            "session_id": session.id,
            "project_dir": str(session.project_dir),
            "request_model": request,
        }
    _executor.submit(_run_team, run_id, request, session.project_dir)
    return {"run_id": run_id, "status": "running"}


@router.get("/runs/{run_id}")
def get_team_run(run_id: str) -> dict[str, Any]:
    run = dict(_get_run(run_id))
    run.pop("project_dir", None)
    run.pop("request_model", None)
    events_path = EvidenceStore(run_id).root / "events.jsonl"
    run["events"] = (
        [json.loads(line) for line in events_path.read_text().splitlines() if line]
        if events_path.exists()
        else []
    )
    report = run.get("report")
    if isinstance(report, TeamRunReport):
        run["report"] = report.model_dump(mode="json")
    return run


@router.get("/runs/{run_id}/report", response_model=TeamRunReport)
def get_team_report(run_id: str) -> TeamRunReport:
    report = _get_run(run_id).get("report")
    if not isinstance(report, TeamRunReport):
        raise HTTPException(status_code=404, detail="this team run has no report yet")
    return report


@router.get("/runs/{run_id}/evidence")
def get_team_evidence(run_id: str) -> dict[str, Any]:
    run = _get_run(run_id)
    path = EvidenceStore(run_id).evidence_path
    records = []
    if path.exists():
        import json

        records = [json.loads(line) for line in path.read_text().splitlines() if line]
    return {"run_id": run_id, "status": run["status"], "records": records}


@router.post("/runs/{run_id}/answer")
def answer_team_run(run_id: str, request: TeamAnswerRequest) -> dict[str, Any]:
    run = _get_run(run_id)
    with _lock:
        run.setdefault("answers", []).append(request.answer)
    evidence = EvidenceStore(run_id)
    evidence.write_artifact("human-answers.json", json.dumps(run["answers"], indent=2) + "\n")
    with (evidence.root / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "human_review_answer", "answer": request.answer}) + "\n")
    report = run.get("report")
    if isinstance(report, TeamRunReport) and report.release_status == "needs_human_review":
        original = run.get("request_model")
        project_dir = run.get("project_dir")
        if isinstance(original, TeamRunRequest) and isinstance(project_dir, str):
            resumed = original.model_copy(
                update={"request": f"{original.request}\nHuman review answer: {request.answer}"}
            )
            with _lock:
                run["status"] = "running"
            _executor.submit(_run_team, run_id, resumed, Path(project_dir))
    return {"run_id": run_id, "accepted": True, "answers": run["answers"]}
