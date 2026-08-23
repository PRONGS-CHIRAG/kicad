"""The actual schematic edits, as primitives both executors share.

Connections are made with global labels placed on pin connection points, which is
what KiCAD's own netlister resolves into nets. Moving an already-labelled pin
replaces its isolated label; shared labels and wires are rejected rather than
silently re-netting unrelated items.

These live here rather than in either executor because there are two of them: the
local executor batches a whole plan into one load/save, while the MCP server
handles one tool call at a time. Sharing the primitives is what makes those two
paths produce the same schematic instead of two implementations that drift.
"""

from __future__ import annotations

from . import sexpr, writer
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
    _validate_pin_net_change(doc, state, pin_ref, net, position)
    current = _current_pin_net(doc, state, pin_ref, position)
    if current and current == net:
        return f"{pin_ref} already on {net}"
    if current and not current.startswith("Net-("):
        writer.remove_labels_at(doc, *position)
    added = writer.add_global_label(doc, net, position[0], position[1])
    return f"labelled {pin_ref} as {net}" if added else f"{pin_ref} already on {net}"


def connect_pins(doc: list, state: ProjectState, from_pin: str, to_pin: str, net: str) -> str:
    validate_pin_connection(doc, state, from_pin, net)
    validate_pin_connection(doc, state, to_pin, net)
    return "; ".join((label_pin(doc, state, from_pin, net), label_pin(doc, state, to_pin, net)))


def connect_pin_to_net(doc: list, state: ProjectState, pin: str, net: str) -> str:
    validate_pin_connection(doc, state, pin, net)
    return label_pin(doc, state, pin, net)


def validate_pin_connection(doc: list, state: ProjectState, pin_ref: str, net: str) -> None:
    _validate_pin_net_change(doc, state, pin_ref, net, _pin_position(state, pin_ref))


def _pin_position(state: ProjectState, pin_ref: str) -> tuple[float, float]:
    reference, _, pin_key = pin_ref.partition(".")
    position = state.pin_position(reference, pin_key)
    if position is None:
        raise ValueError(f"pin {pin_ref} not found in schematic")
    return position


def _validate_pin_net_change(
    doc: list,
    state: ProjectState,
    pin_ref: str,
    net: str,
    position: tuple[float, float],
) -> None:
    reference, _, pin_key = pin_ref.partition(".")
    component = state.components.get(reference)
    if component is None:
        raise ValueError(f"pin {pin_ref} not found in schematic")
    if component.is_power:
        raise ValueError(
            f"rejected {pin_ref}: power symbols and power flags are net markers, not connectable pins"
        )
    pin = component.pin(pin_key)
    if pin is None:
        raise ValueError(f"pin {pin_ref} not found in schematic")
    labels = writer.labels_at(doc, *position)
    current = labels[0][1] if len(labels) == 1 else state.pin_nets.get(f"{reference}.{pin.number}")
    if not current or current == net or current.startswith("Net-("):
        return
    if len(labels) != 1 or labels[0][1] != current:
        raise ValueError(
            f"cannot move {pin_ref} from {current} to {net}: "
            "its existing net label is not uniquely attached to this pin"
        )
    if _point_is_shared(doc, state, pin_ref, position):
        raise ValueError(
            f"cannot move {pin_ref} from {current} to {net}: "
            "its existing net label is shared with other schematic items"
        )


def _current_pin_net(
    doc: list,
    state: ProjectState,
    pin_ref: str,
    position: tuple[float, float],
) -> str | None:
    labels = writer.labels_at(doc, *position)
    if len(labels) == 1:
        return labels[0][1]
    reference, _, pin_key = pin_ref.partition(".")
    pin = state.components[reference].pin(pin_key)
    assert pin is not None
    return state.pin_nets.get(f"{reference}.{pin.number}")


def _point_is_shared(
    doc: list,
    state: ProjectState,
    pin_ref: str,
    position: tuple[float, float],
) -> bool:
    for other_ref, other_position in state.pin_positions.items():
        if other_ref != pin_ref and _same_point(other_position, position):
            return True
    for wire in sexpr.find_all(doc, "wire"):
        points = sexpr.find(wire, "pts")
        if points is None:
            continue
        coordinates = [
            (sexpr.number(point, 1), sexpr.number(point, 2)) for point in sexpr.find_all(points, "xy")
        ]
        if any(
            _point_on_segment(position, start, end)
            for start, end in zip(coordinates, coordinates[1:], strict=False)
        ):
            return True
    return False


def _same_point(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return abs(left[0] - right[0]) < 0.01 and abs(left[1] - right[1]) < 0.01


def _point_on_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    if _same_point(start, end):
        return False
    cross = (point[0] - start[0]) * (end[1] - start[1]) - (point[1] - start[1]) * (end[0] - start[0])
    if abs(cross) >= 0.01:
        return False
    return (
        min(start[0], end[0]) - 0.01 <= point[0] <= max(start[0], end[0]) + 0.01
        and min(start[1], end[1]) - 0.01 <= point[1] <= max(start[1], end[1]) + 0.01
    )


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
