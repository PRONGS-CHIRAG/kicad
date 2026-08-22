"""Executor that edits the KiCAD schematic files directly.

The edits themselves live in `app.kicad.edits`, shared with the MCP server, so
the two execution paths cannot drift into two behaviours. This one batches a
whole plan into a single load/save.
"""

from __future__ import annotations

from pathlib import Path

from ..kicad import edits, writer
from ..kicad.reader import ProjectState, read_project
from ..models import ActionPlan, ConnectPins, ConnectPinToNet, EnsurePullup, ExecutionResult, ExecutionStep
from .base import Executor


class LocalExecutor(Executor):
    name = "local"

    def __init__(self, max_actions: int | None = None) -> None:
        # max_actions exists so the benchmark can simulate a halfway failure.
        self.max_actions = max_actions

    def execute(self, project_dir: Path, plan: ActionPlan) -> ExecutionResult:
        project_dir = Path(project_dir)
        state = read_project(project_dir)
        doc = writer.load(state.schematic_path)
        project_name = state.schematic_path.stem
        steps: list[ExecutionStep] = []
        pullup_slot = 0

        for index, action in enumerate(plan.actions):
            if self.max_actions is not None and index >= self.max_actions:
                writer.save(doc, state.schematic_path)
                return ExecutionResult(
                    completed=False,
                    steps=steps,
                    error=f"execution stopped after {index} of {len(plan.actions)} actions",
                )
            try:
                if isinstance(action, ConnectPins):
                    step = self._connect_pins(doc, state, action)
                elif isinstance(action, ConnectPinToNet):
                    step = self._connect_pin_to_net(doc, state, action)
                elif isinstance(action, EnsurePullup):
                    step = self._ensure_pullup(doc, state, action, project_name, pullup_slot)
                    if step.status == "applied":
                        pullup_slot += 1
                else:  # pragma: no cover - guarded by the schema
                    raise TypeError(f"unsupported action type {action.type}")
            except Exception as exc:  # noqa: BLE001 - surfaced as a failed step
                steps.append(
                    ExecutionStep(action_id=action.id, tool=self.name, status="failed", detail=str(exc))
                )
                writer.save(doc, state.schematic_path)
                return ExecutionResult(completed=False, steps=steps, error=str(exc))
            steps.append(step)

        writer.save(doc, state.schematic_path)
        return ExecutionResult(completed=True, steps=steps)

    def _connect_pins(self, doc: list, state: ProjectState, action: ConnectPins) -> ExecutionStep:
        return ExecutionStep(
            action_id=action.id,
            tool=self.name,
            status="applied",
            detail=edits.connect_pins(doc, state, action.from_pin, action.to_pin, action.net_name),
        )

    def _connect_pin_to_net(self, doc: list, state: ProjectState, action: ConnectPinToNet) -> ExecutionStep:
        return ExecutionStep(
            action_id=action.id,
            tool=self.name,
            status="applied",
            detail=edits.connect_pin_to_net(doc, state, action.pin, action.net),
        )

    def _ensure_pullup(
        self,
        doc: list,
        state: ProjectState,
        action: EnsurePullup,
        project_name: str,
        slot: int,
    ) -> ExecutionStep:
        existing = self._find_pullup(state, action.net, action.to_net)
        if existing:
            return ExecutionStep(
                action_id=action.id,
                tool=self.name,
                status="skipped",
                detail=f"{existing} already pulls {action.net} up to {action.to_net}",
            )
        return ExecutionStep(
            action_id=action.id,
            tool=self.name,
            status="applied",
            detail=edits.place_pullup(
                doc, action.net, action.to_net, action.value, project_name, slot=slot
            ),
        )

    @staticmethod
    def _find_pullup(state: ProjectState, net: str, rail: str) -> str | None:
        """A seam on purpose: tests subclass this to be blind and force a duplicate."""
        return edits.find_pullup(state, net, rail)
