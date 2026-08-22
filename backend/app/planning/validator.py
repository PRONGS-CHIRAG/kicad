"""Rule-based validation of a plan before it is allowed anywhere near KiCAD."""

from __future__ import annotations

from ..kicad.reader import ProjectState
from ..models import (
    ActionPlan,
    ActionType,
    ConnectPins,
    ConnectPinToNet,
    EnsurePullup,
    PlaceFootprint,
    Protocol,
)

ALLOWED_ACTION_TYPES = {
    ActionType.CONNECT_PINS,
    ActionType.CONNECT_PIN_TO_NET,
    ActionType.ENSURE_PULLUP,
    ActionType.PLACE_FOOTPRINT,
}
GROUND_NETS = {"GND", "GNDA", "VSS", "AGND"}
POWER_NET_PREFIXES = ("+", "VCC", "VDD", "VBUS")


def _split_pin(reference_pin: str) -> tuple[str, str]:
    reference, _, pin = reference_pin.partition(".")
    return reference, pin


def _pin_exists(state: ProjectState, reference_pin: str) -> str | None:
    reference, pin_key = _split_pin(reference_pin)
    component = state.components.get(reference)
    if component is None:
        return f"component {reference} does not exist"
    if not pin_key:
        return f"no pin specified for {reference}"
    if component.pin(pin_key) is None:
        return f"pin {pin_key} does not exist on {reference}"
    return None


def _is_power_net(net: str) -> bool:
    upper = net.upper()
    return upper in GROUND_NETS or upper.startswith(POWER_NET_PREFIXES)


def validate_plan(state: ProjectState, plan: ActionPlan) -> list[str]:
    """Return a list of blocking problems; an empty list means the plan may run."""
    problems: list[str] = []

    if plan.protocol != Protocol.I2C:
        problems.append(f"protocol {plan.protocol} is not supported")
    if not plan.actions:
        problems.append("plan contains no actions")

    for reference in plan.selected_components:
        if reference not in state.components:
            problems.append(f"selected component {reference} does not exist")

    protected = {item.upper() for item in plan.protected_objects}
    seen_ids: set[str] = set()

    for action in plan.actions:
        if action.type not in ALLOWED_ACTION_TYPES:
            problems.append(f"action type {action.type} is not allowed")
            continue
        if action.id in seen_ids:
            problems.append(f"duplicate action id {action.id}")
        seen_ids.add(action.id)

        pins: list[str] = []
        nets: list[str] = []
        if isinstance(action, ConnectPins):
            pins = [action.from_pin, action.to_pin]
            nets = [action.net_name]
            if action.from_pin == action.to_pin:
                problems.append(f"{action.id}: cannot connect a pin to itself")
        elif isinstance(action, ConnectPinToNet):
            pins = [action.pin]
            nets = [action.net]
        elif isinstance(action, EnsurePullup):
            nets = [action.net, action.to_net]
            if _is_power_net(action.net):
                problems.append(f"{action.id}: refusing to pull up power net {action.net}")
            if not _is_power_net(action.to_net):
                problems.append(f"{action.id}: pull-up target {action.to_net} is not a power net")
            if action.to_net.upper() in GROUND_NETS:
                problems.append(f"{action.id}: pull-up to ground is not valid")
        elif isinstance(action, PlaceFootprint):
            nets = [action.net]
            if action.near not in state.components:
                problems.append(f"{action.id}: placement target {action.near} does not exist")
            elif action.near.upper() in protected:
                problems.append(f"{action.id}: placement target {action.near} is protected")
            if not any(
                isinstance(other, EnsurePullup) and other.net == action.net for other in plan.actions
            ):
                problems.append(
                    f"{action.id}: no pull-up is being added on {action.net} for this placement to attach to"
                )

        for pin in pins:
            error = _pin_exists(state, pin)
            if error:
                problems.append(f"{action.id}: {error}")
                continue
            reference, pin_key = _split_pin(pin)
            component = state.components[reference]
            pin_obj = component.pin(pin_key)
            if reference.upper() in protected or pin.upper() in protected:
                problems.append(f"{action.id}: {pin} is protected and must not change")
            if isinstance(action, ConnectPinToNet) and pin_obj.electrical_type == "power_in":
                net_upper = action.net.upper()
                if "GND" in pin_obj.name.upper() and net_upper not in GROUND_NETS:
                    problems.append(f"{action.id}: ground pin {pin} would be tied to power net {action.net}")
                if "GND" not in pin_obj.name.upper() and net_upper in GROUND_NETS:
                    problems.append(f"{action.id}: power pin {pin} would be tied to ground")
            if isinstance(action, ConnectPins) and pin_obj.electrical_type == "output":
                problems.append(f"{action.id}: {pin} is an output and cannot be shared on an I2C bus")

        for net in nets:
            if not net or net.strip() != net:
                problems.append(f"{action.id}: invalid net name {net!r}")
            if net.upper() in protected:
                problems.append(f"{action.id}: net {net} is protected and must not change")

    signal_nets = {a.net_name for a in plan.actions if isinstance(a, ConnectPins)}
    if len(signal_nets) < len([a for a in plan.actions if isinstance(a, ConnectPins)]):
        problems.append("SDA and SCL must not share the same net")
    for net in signal_nets:
        if _is_power_net(net):
            problems.append(f"signal net {net} collides with a power net")

    return problems
