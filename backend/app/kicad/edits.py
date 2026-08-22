"""The actual schematic edits, as primitives both executors share.

Connections are made with global labels placed on pin connection points, which is
what KiCAD's own netlister resolves into nets.

These live here rather than in either executor because there are two of them: the
local executor batches a whole plan into one load/save, while the MCP server
handles one tool call at a time. Sharing the primitives is what makes those two
paths produce the same schematic instead of two implementations that drift.
"""

from __future__ import annotations

from . import writer
from .reader import ProjectState

# Horizontal step between pull-ups added in the same batch. `free_area` already
# moves down a row for each symbol already on the sheet, so this only spreads
# parts that a single batch places before any of them is on disk.
PULLUP_SPACING = 12.7


def label_pin(doc: list, state: ProjectState, pin_ref: str, net: str) -> str:
    """Put `net` on the connection point of `pin_ref` (REF.PIN)."""
    reference, _, pin_key = pin_ref.partition(".")
    position = state.pin_position(reference, pin_key)
    if position is None:
        raise ValueError(f"pin {pin_ref} not found in schematic")
    added = writer.add_global_label(doc, net, position[0], position[1])
    return f"labelled {pin_ref} as {net}" if added else f"{pin_ref} already on {net}"


def connect_pins(doc: list, state: ProjectState, from_pin: str, to_pin: str, net: str) -> str:
    return "; ".join(
        (label_pin(doc, state, from_pin, net), label_pin(doc, state, to_pin, net))
    )


def connect_pin_to_net(doc: list, state: ProjectState, pin: str, net: str) -> str:
    return label_pin(doc, state, pin, net)


def find_pullup(state: ProjectState, net: str, rail: str) -> str | None:
    """Reference of a resistor already bridging `net` and `rail`, if there is one."""
    for reference, component in state.components.items():
        if component.is_power or not reference.startswith("R"):
            continue
        nets = {state.pin_nets.get(f"{reference}.{pin.number}") for pin in component.pins}
        if net in nets and rail in nets:
            return reference
    return None


def place_pullup(
    doc: list,
    net: str,
    to_net: str,
    value: str,
    project_name: str,
    slot: int = 0,
) -> str:
    """Place a resistor bridging `net` and `to_net`, unconditionally.

    Deliberately does no "is one already there?" check: that belongs to the
    caller, so an executor can be subclassed into being blind to existing parts
    and the duplicate-component checks can be tested against a real fault.

    `slot` only matters while several pull-ups are placed against one in-memory
    document; a caller handling one action at a time can leave it at zero,
    because `free_area` accounts for whatever is already on the sheet.
    """
    writer.ensure_lib_symbol(doc, "Device:R", writer.R_LIB_SYMBOL)
    reference = writer.next_reference(doc, "R")
    base_x, base_y = writer.free_area(doc)
    x = base_x + slot * PULLUP_SPACING
    writer.add_symbol_instance(doc, "Device:R", reference, value, x, base_y, project_name)
    writer.add_global_label(doc, to_net, x, base_y - 3.81)
    writer.add_global_label(doc, net, x, base_y + 3.81)
    return f"added {reference} ({value}) between {net} and {to_net}"


def add_pullup(
    doc: list,
    state: ProjectState,
    net: str,
    to_net: str,
    value: str,
    project_name: str,
    slot: int = 0,
) -> tuple[str, bool]:
    """Check-then-place. Returns (detail, added); `added` is False for a skip."""
    existing = find_pullup(state, net, to_net)
    if existing:
        return f"{existing} already pulls {net} up to {to_net}", False
    return place_pullup(doc, net, to_net, value, project_name, slot=slot), True
