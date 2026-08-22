"""Rule-based validation of a plan before it is allowed anywhere near KiCAD."""

from __future__ import annotations

from ..kicad.reader import ProjectState
from ..models import ActionPlan, ActionType, ConnectPins, ConnectPinToNet, EnsurePullup, Protocol
from .protocols import spec_for

ALLOWED_ACTION_TYPES = {ActionType.CONNECT_PINS, ActionType.CONNECT_PIN_TO_NET, ActionType.ENSURE_PULLUP}
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

    spec = spec_for(plan.protocol)
    if spec is None:
        problems.append(f"protocol {plan.protocol} is not supported")
    # I2C is an open-drain bus, so ANY output on a shared signal is a fault. On
    # SPI/UART/GPIO a controller output driving a peripheral input is the normal
    # case, and only output-against-output is a conflict.
    shared_outputs_forbidden = spec.shared_outputs_forbidden if spec is not None else True
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
            if (
                shared_outputs_forbidden
                and isinstance(action, ConnectPins)
                and pin_obj.electrical_type == "output"
            ):
                problems.append(
                    f"{action.id}: {pin} is an output and cannot be shared on "
                    f"an {plan.protocol.value} bus"
                )

        if not shared_outputs_forbidden and isinstance(action, ConnectPins):
            endpoints = []
            for pin in pins:
                reference, pin_key = _split_pin(pin)
                component = state.components.get(reference)
                endpoints.append(component.pin(pin_key) if component is not None else None)
            if all(p is not None and p.electrical_type == "output" for p in endpoints):
                problems.append(
                    f"{action.id}: {action.from_pin} and {action.to_pin} are both outputs "
                    "and cannot be connected"
                )

        for net in nets:
            if not net or net.strip() != net:
                problems.append(f"{action.id}: invalid net name {net!r}")
            if net.upper() in protected:
                problems.append(f"{action.id}: net {net} is protected and must not change")

    signal_nets = {a.net_name for a in plan.actions if isinstance(a, ConnectPins)}
    if len(signal_nets) < len([a for a in plan.actions if isinstance(a, ConnectPins)]):
        if plan.protocol is Protocol.I2C:
            problems.append("SDA and SCL must not share the same net")
        else:
            problems.append(f"{plan.protocol.value} signals must not share the same net")
    for net in signal_nets:
        if _is_power_net(net):
            problems.append(f"signal net {net} collides with a power net")

    return problems
