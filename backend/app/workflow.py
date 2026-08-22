"""Session orchestration: checkpoint -> execute -> validate -> accept or restore."""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings
from .decision import evaluate
from .execution.base import Executor
from .execution.local import LocalExecutor
from .execution.mitos import MitosExecutor
from .kicad.board import BoardSync
from .kicad.board import sync_to_schematic as sync_board
from .kicad.checkpoint import Checkpoint
from .kicad.erc import KicadCli, diff_violations
from .kicad.reader import ProjectState, read_project
from .models import (
    ActionPlan,
    Clarification,
    Decision,
    ErcReport,
    ExecutionResult,
    PlanAnswers,
    RunReport,
)
from .planning.llm import plan_from_instruction
from .planning.validator import validate_plan

logger = logging.getLogger(__name__)


def build_executor() -> Executor:
    if settings.executor == "mitos":
        return MitosExecutor(settings.mitos_command)
    return LocalExecutor()


@dataclass
class Session:
    id: str
    source_project: Path
    project_dir: Path
    baseline_erc: ErcReport
    state: ProjectState
    baseline_drc: ErcReport | None = None
    plan: ActionPlan | None = None
    plan_source: str = "rules"
    clarification: Clarification | None = None
    report: RunReport | None = None
    history: list[str] = field(default_factory=list)
    revision: int = 0
    """Bumped whenever the on-disk project changes, so renders are never served stale."""

    @property
    def board_path(self) -> Path | None:
        boards = sorted(self.project_dir.glob("*.kicad_pcb"))
        return boards[0] if boards else None


class SessionStore:
    """In-memory session state; each session owns a scratch copy of the project."""

    def __init__(self, cli: KicadCli | None = None) -> None:
        self.cli = cli or KicadCli(settings.kicad_cli)
        self._sessions: dict[str, Session] = {}

    def project_roots(self) -> list[tuple[Path, str]]:
        """Fixture projects plus uploaded ones, in lookup order."""
        return [(Path(settings.projects_dir), "fixture"), (settings.uploads_dir, "uploaded")]

    def list_projects(self) -> list[dict]:
        projects: list[dict] = []
        seen: set[str] = set()
        for root, origin in self.project_roots():
            if not root.exists():
                continue
            for path in sorted(root.iterdir()):
                if path.name in seen or not path.is_dir() or not list(path.glob("*.kicad_sch")):
                    continue
                seen.add(path.name)
                projects.append({"name": path.name, "path": str(path), "origin": origin})
        return projects

    def resolve_project(self, project_name: str) -> Path:
        """Locate a project by name across every root, refusing anything path-like."""
        if project_name != Path(project_name).name or project_name in {"", ".", ".."}:
            raise FileNotFoundError(f"invalid project name {project_name!r}")
        for root, _ in self.project_roots():
            candidate = root / project_name
            if candidate.is_dir() and list(candidate.glob("*.kicad_sch")):
                return candidate
        raise FileNotFoundError(f"unknown project {project_name}")

    def create(self, project_name: str) -> Session:
        source = self.resolve_project(project_name)
        if not source.is_dir():
            raise FileNotFoundError(f"unknown project {project_name}")
        session_id = uuid.uuid4().hex[:12]
        work_dir = settings.sessions_dir / session_id / "project"
        work_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, work_dir)
        state = read_project(work_dir)
        baseline = self.cli.run_erc(state.schematic_path)
        session = Session(
            id=session_id,
            source_project=source,
            project_dir=work_dir,
            baseline_erc=baseline,
            state=state,
        )
        board = session.board_path
        session.baseline_drc = self.cli.run_drc(board) if board else None
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def refresh(self, session: Session) -> ProjectState:
        session.state = read_project(session.project_dir)
        return session.state

    def render(self, session: Session, view: str) -> str | None:
        """Render the session's working copy — never the plan — so the view shows only applied changes."""
        if view == "pcb":
            board = session.board_path
            return self.cli.export_board_svg(board) if board else None
        return self.cli.export_schematic_svg(session.state.schematic_path)

    def plan(
        self,
        session: Session,
        selected: list[str],
        instruction: str,
        answers: PlanAnswers | None = None,
    ) -> tuple[ActionPlan | Clarification, list[str], str]:
        self.refresh(session)
        result, source = plan_from_instruction(session.state, selected, instruction, answers)
        session.plan_source = source
        if isinstance(result, Clarification):
            session.plan, session.clarification = None, result
            return result, [], source
        problems = validate_plan(session.state, result)
        session.plan = None if problems else result
        session.clarification = None
        return result, problems, source

    def execute(self, session: Session, plan: ActionPlan, executor: Executor | None = None) -> RunReport:
        executor = executor or build_executor()
        started = time.perf_counter()
        before = self.refresh(session)
        checkpoint = Checkpoint.create(session.project_dir, settings.checkpoints_dir / session.id)

        try:
            execution = executor.execute(session.project_dir, plan)
        except Exception as exc:  # noqa: BLE001 - executor failures are a rejection, not a crash
            logger.exception("executor raised")
            execution = ExecutionResult(completed=False, steps=[], error=str(exc))

        # Reading the schematic back is a pure parse, so it runs before the file
        # snapshot — the board sync needs the state it produces.
        after: ProjectState | None
        try:
            after = read_project(session.project_dir)
        except Exception as exc:  # noqa: BLE001 - unreadable project is a hard failure
            logger.warning("post-execution read failed: %s", exc)
            after = None

        # Bring the board in line with the schematic that was just written.
        # kicad-cli has no "Update PCB from Schematic", so this is ours to do. It
        # sits inside the checkpoint envelope, so a bad board write is reverted
        # along with everything else.
        board = session.board_path
        board_sync = BoardSync()
        if board and after is not None and execution.completed:
            try:
                board_sync = sync_board(board, after)
            except Exception as exc:  # noqa: BLE001 - a failed sync must not crash the run
                logger.exception("board sync failed")
                execution = execution.model_copy(update={"error": f"board sync failed: {exc}"})

        # Snapshot the file-level diff here, before ERC/DRC run: kicad-cli
        # rewrites project files as a side effect, and attributing those writes
        # to the executor would reject every run on a machine that has KiCAD.
        files_changed = checkpoint.files_changed()

        erc_after: ErcReport | None = None
        if after is not None:
            try:
                erc_after = self.cli.run_erc(after.schematic_path)
                if not erc_after.ran and self.cli.available:
                    after = None
            except Exception as exc:  # noqa: BLE001
                logger.warning("post-execution ERC failed: %s", exc)
                after, erc_after = None, None

        drc_after = self.cli.run_drc(board) if board else None
        drc_before = session.baseline_drc
        drc_diff = (
            diff_violations(drc_before, drc_after)
            if drc_before and drc_before.ran and drc_after and drc_after.ran
            else None
        )

        violation_diff = (
            diff_violations(session.baseline_erc, erc_after) if erc_after and erc_after.ran else None
        )
        decision, reason, validation = evaluate(
            plan=plan,
            execution=execution,
            before=before,
            after=after,
            erc_before=session.baseline_erc,
            erc_after=erc_after,
            violation_diff=violation_diff,
            files_changed=files_changed,
            drc_before=drc_before,
            drc_after=drc_after,
            drc_diff=drc_diff,
            board_sync=board_sync,
        )

        restoration_verified: bool | None = None
        if decision == Decision.REJECTED_AND_RESTORED:
            restoration_verified = checkpoint.restore()
            self.refresh(session)

        # The board sync is part of what the run changed, so it is reported
        # alongside the executor's own steps rather than left implicit.
        changes = [step.detail for step in execution.steps if step.status == "applied"]
        changes.extend(board_sync.summary())
        report = RunReport(
            session_id=session.id,
            goal=plan.goal,
            decision=decision,
            reason=reason,
            changes=changes if decision != Decision.REJECTED_AND_RESTORED else [],
            restoration_verified=restoration_verified,
            plan=plan,
            execution=execution,
            validation=validation,
            duration_seconds=round(time.perf_counter() - started, 3),
        )
        session.report = report
        session.history.append(f"{decision.value}: {reason}")
        session.revision += 1
        if decision == Decision.ACCEPTED:
            session.baseline_erc = erc_after or session.baseline_erc
            session.baseline_drc = drc_after or session.baseline_drc
        self.refresh(session)
        return report


store = SessionStore()
