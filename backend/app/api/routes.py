"""HTTP API for the KiCAD Mitos copilot."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from ..config import settings
from ..kicad.erc import KicadCli
from ..kicad.upload import UploadError, extract_project
from ..models import ActionPlan, Clarification, ErcReport, PlanAnswers, RunReport
from ..planning.validator import validate_plan
from ..workflow import Session, store

router = APIRouter(prefix="/api")


class CreateSessionRequest(BaseModel):
    project: str


class SessionResponse(BaseModel):
    session_id: str
    project: str
    project_dir: str
    components: list[dict]
    nets: dict[str, list[str]]
    baseline_erc: ErcReport
    baseline_drc: ErcReport | None = None
    revision: int = 0
    has_pcb: bool = False


class PlanRequest(BaseModel):
    selected_components: list[str] = Field(min_length=1)
    instruction: str
    answers: PlanAnswers = Field(default_factory=PlanAnswers)


class PlanResponse(BaseModel):
    plan: ActionPlan | None = None
    clarification: Clarification | None = None
    problems: list[str] = Field(default_factory=list)
    source: str = "rules"
    executable: bool = False


class ExecuteRequest(BaseModel):
    approved: bool = True
    plan: ActionPlan | None = None


def _session_response(session: Session) -> SessionResponse:
    return SessionResponse(
        session_id=session.id,
        project=session.source_project.name,
        project_dir=str(session.project_dir),
        components=session.state.component_summaries(),
        nets={name: sorted(pins) for name, pins in sorted(session.state.nets.items())},
        baseline_erc=session.baseline_erc,
        baseline_drc=session.baseline_drc,
        revision=session.revision,
        has_pcb=session.board_path is not None,
    )


@router.get("/health")
def health() -> dict:
    cli = KicadCli(settings.kicad_cli)
    return {
        "status": "ok",
        "kicad_cli": cli.version(),
        "erc_supported": cli.supports("sch", "erc"),
        "drc_supported": cli.supports("pcb", "drc"),
        "executor": settings.executor,
        "llm_enabled": settings.llm_enabled,
        "team_runner": settings.resolved_team_runner,
        "team_parallel": settings.team_parallel,
    }


MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.get("/projects")
def list_projects() -> dict:
    return {"projects": store.list_projects()}


@router.post("/projects", status_code=201)
async def upload_project(
    name: str = Form(...),
    file: UploadFile = File(...),  # noqa: B008 - FastAPI resolves these at request time
) -> dict:
    """Accept a zipped KiCAD project. Uploads land in the workspace, not the fixtures."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"archive exceeds {MAX_UPLOAD_BYTES} bytes")
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    try:
        target = extract_project(data, name.strip(), settings.uploads_dir)
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": target.name, "path": str(target), "origin": "uploaded"}


@router.post("/sessions", response_model=SessionResponse)
def create_session(request: CreateSessionRequest) -> SessionResponse:
    try:
        session = store.create(request.project)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _session_response(session)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str) -> SessionResponse:
    try:
        session = store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}") from exc
    store.refresh(session)
    return _session_response(session)


@router.post("/sessions/{session_id}/plan", response_model=PlanResponse)
def create_plan(session_id: str, request: PlanRequest) -> PlanResponse:
    try:
        session = store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}") from exc
    result, problems, source = store.plan(
        session, request.selected_components, request.instruction, request.answers
    )
    if isinstance(result, Clarification):
        return PlanResponse(clarification=result, source=source)
    return PlanResponse(plan=result, problems=problems, source=source, executable=not problems)


@router.get("/sessions/{session_id}/render")
def render_session(session_id: str, view: str = "schematic") -> Response:
    """SVG of the working copy as it exists on disk right now."""
    try:
        session = store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}") from exc
    if view not in {"schematic", "pcb"}:
        raise HTTPException(status_code=400, detail=f"unknown view {view}")
    svg = store.render(session, view)
    if svg is None:
        detail = (
            "this project has no board file" if view == "pcb" else "kicad-cli could not render the schematic"
        )
        raise HTTPException(status_code=404, detail=detail)
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.post("/sessions/{session_id}/execute", response_model=RunReport)
def execute(session_id: str, request: ExecuteRequest) -> RunReport:
    try:
        session = store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}") from exc
    if not request.approved:
        raise HTTPException(status_code=400, detail="the plan must be approved before execution")
    plan = request.plan or session.plan
    if plan is None:
        raise HTTPException(status_code=400, detail="no validated plan available for this session")
    if request.plan is not None:
        problems = validate_plan(store.refresh(session), request.plan)
        if problems:
            raise HTTPException(status_code=400, detail="; ".join(problems))
    return store.execute(session, plan)


@router.get("/sessions/{session_id}/report", response_model=RunReport)
def get_report(session_id: str) -> RunReport:
    try:
        session = store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}") from exc
    if session.report is None:
        raise HTTPException(status_code=404, detail="this session has no execution report yet")
    return session.report
