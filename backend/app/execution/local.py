"""Executor that edits the KiCAD schematic files directly.

Connections are made with global labels placed on the pin connection points,
which is what KiCAD's own netlist and ERC engine resolve into nets.
"""

from __future__ import annotations

from pathlib import Path

from ..kicad import writer
from ..kicad.reader import ProjectState, read_project
from ..models import ActionPlan, ConnectPins, ConnectPinToNet, EnsurePullup, ExecutionResult, ExecutionStep
from .base import Executor

PULLUP_SPACING = 12.7


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

    def _label_pin(self, doc: list, state: ProjectState, pin_ref: str, net: str) -> str:
        reference, _, pin_key = pin_ref.partition(".")
        position = state.pin_position(reference, pin_key)
        if position is None:
            raise ValueError(f"pin {pin_ref} not found in schematic")
        added = writer.add_global_label(doc, net, position[0], position[1])
        return f"labelled {pin_ref} as {net}" if added else f"{pin_ref} already on {net}"

    def _connect_pins(self, doc: list, state: ProjectState, action: ConnectPins) -> ExecutionStep:
        details = [
            self._label_pin(doc, state, action.from_pin, action.net_name),
            self._label_pin(doc, state, action.to_pin, action.net_name),
        ]
        return ExecutionStep(action_id=action.id, tool=self.name, status="applied", detail="; ".join(details))

    def _connect_pin_to_net(self, doc: list, state: ProjectState, action: ConnectPinToNet) -> ExecutionStep:
        return ExecutionStep(
            action_id=action.id,
            tool=self.name,
            status="applied",
            detail=self._label_pin(doc, state, action.pin, action.net),
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
        writer.ensure_lib_symbol(doc, "Device:R", writer.R_LIB_SYMBOL)
        reference = writer.next_reference(doc, "R")
        base_x, base_y = writer.free_area(doc)
        x = base_x + slot * PULLUP_SPACING
        writer.add_symbol_instance(doc, "Device:R", reference, action.value, x, base_y, project_name)
        writer.add_global_label(doc, action.to_net, x, base_y - 3.81)
        writer.add_global_label(doc, action.net, x, base_y + 3.81)
        return ExecutionStep(
            action_id=action.id,
            tool=self.name,
            status="applied",
            detail=f"added {reference} ({action.value}) between {action.net} and {action.to_net}",
        )

    @staticmethod
    def _find_pullup(state: ProjectState, net: str, rail: str) -> str | None:
        for reference, component in state.components.items():
            if component.is_power or not reference.startswith("R"):
                continue
            nets = {state.pin_nets.get(f"{reference}.{pin.number}") for pin in component.pins}
            if net in nets and rail in nets:
                return reference
        return None
