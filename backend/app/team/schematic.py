"""Apply validated schematic intents through the shared KiCAD editors."""

from __future__ import annotations

from pathlib import Path

from ..kicad import edits, writer
from ..kicad.reader import ProjectState, read_project
from .schemas import (
    ConnectPinsIntent,
    ConnectPinToNetIntent,
    EnsurePullupIntent,
    SchematicIntents,
)


def apply_schematic_intents(intents: SchematicIntents, project_dir: Path) -> None:
    """Apply all intents atomically after validating their schematic references."""
    state = read_project(project_dir)
    document = writer.load(state.schematic_path)
    for intent in intents.intents:
        if isinstance(intent, ConnectPinsIntent):
            _require_pin(state, intent.from_pin)
            _require_pin(state, intent.to_pin)
            edits.validate_pin_connection(document, state, intent.from_pin, intent.net_name)
            edits.validate_pin_connection(document, state, intent.to_pin, intent.net_name)
        elif isinstance(intent, ConnectPinToNetIntent):
            _require_pin(state, intent.pin)
            edits.validate_pin_connection(document, state, intent.pin, intent.net)
        elif isinstance(intent, EnsurePullupIntent):
            if not intent.net or not intent.to_net:
                raise ValueError("pull-up intent must name both nets")
        else:  # pragma: no cover - protected by the discriminated union
            raise TypeError(f"unsupported schematic intent: {type(intent).__name__}")

    pullup_slot = 0
    for intent in intents.intents:
        if isinstance(intent, ConnectPinsIntent):
            edits.connect_pins(document, state, intent.from_pin, intent.to_pin, intent.net_name)
        elif isinstance(intent, ConnectPinToNetIntent):
            edits.connect_pin_to_net(document, state, intent.pin, intent.net)
        else:
            _detail, added = edits.add_pullup(
                document,
                state,
                intent.net,
                intent.to_net,
                intent.value,
                state.schematic_path.stem,
                slot=pullup_slot,
            )
            if added:
                pullup_slot += 1
    if intents.intents:
        writer.save(document, state.schematic_path)


def _require_pin(state: ProjectState, pin_ref: str) -> None:
    reference, _, pin_key = pin_ref.partition(".")
    if not pin_key or state.pin_position(reference, pin_key) is None:
        raise ValueError(f"pin {pin_ref} not found in schematic")
    component = state.components[reference]
    if component.is_power:
        raise ValueError(
            f"rejected {pin_ref}: power symbols and power flags are net markers, not connectable pins"
        )
