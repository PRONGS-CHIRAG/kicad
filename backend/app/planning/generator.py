"""Turn an instruction plus real KiCAD state into a structured action plan."""

from __future__ import annotations

import re
from dataclasses import replace

from ..kicad.reader import Component, ProjectState
from ..models import (
    ActionPlan,
    Clarification,
    ConnectPins,
    ConnectPinToNet,
    EnsurePullup,
    PlanAnswers,
    Protocol,
)
from .instruction import ParsedInstruction, parse_instruction

SUPPORTED_PROTOCOLS = {Protocol.I2C.value}
CONTROLLER_LIB_HINTS = ("mcu", "esp32", "esp8266", "stm32", "atmega", "rp2040", "microcontroller", "cpu")
CONTROLLER_PIN_HINT = re.compile(r"^(GPIO|IO|P[A-D])\d+$", re.IGNORECASE)
SDA_NAMES = ("SDA", "SDIO", "I2C_SDA", "DATA")
SCL_NAMES = ("SCL", "SCK", "I2C_SCL", "CLK")
VCC_NAMES = ("VCC", "VDD", "V+", "3V3", "VIN", "VS")
GND_NAMES = ("GND", "VSS", "AGND")

VOLTAGE_RAIL_ALIASES = {
    "3.3V": ("+3V3", "3V3", "+3.3V", "VDD3V3"),
    "5V": ("+5V", "5V"),
    "1.8V": ("+1V8", "1V8"),
}


def _pin_by_names(component: Component, names: tuple[str, ...]) -> str | None:
    for candidate in names:
        pin = component.pin(candidate)
        if pin is not None:
            return pin.name
    return None


def _answered_pin(component: Component, answer: str | None) -> str | None:
    """Resolve a clarification answer such as "3:SDA", "3" or "SDA" to a pin name."""
    if not answer:
        return None
    for token in (answer, answer.split(":", 1)[0], answer.split(":", 1)[-1]):
        pin = component.pin(token.strip())
        if pin is not None:
            return pin.name
    return None


def _is_controller(component: Component) -> bool:
    lib = component.lib_id.lower()
    if any(hint in lib for hint in CONTROLLER_LIB_HINTS):
        return True
    return sum(1 for pin in component.pins if CONTROLLER_PIN_HINT.match(pin.name)) >= 2


def _known_nets(state: ProjectState) -> set[str]:
    return set(state.nets)


def _resolve_rail(state: ProjectState, voltage: str) -> str | None:
    aliases = VOLTAGE_RAIL_ALIASES.get(voltage, (voltage,))
    nets = _known_nets(state)
    for alias in aliases:
        if alias in nets:
            return alias
    return None


def _ground_net(state: ProjectState) -> str:
    for candidate in ("GND", "GNDA", "VSS"):
        if candidate in state.nets:
            return candidate
    return "GND"


def _existing_pullup(state: ProjectState, net: str, rail: str) -> tuple[str, str] | None:
    """Return (reference, value) of a resistor already tying `net` to `rail`."""
    for reference, component in state.components.items():
        if component.is_power or not reference.startswith("R"):
            continue
        pin_nets = {state.pin_nets.get(f"{reference}.{pin.number}") for pin in component.pins}
        if net in pin_nets and rail in pin_nets:
            return reference, component.value
    return None


def _supply_net(state: ProjectState, component: Component) -> str | None:
    for pin in component.pins:
        if pin.electrical_type == "power_in" and "GND" not in pin.name.upper():
            net = state.pin_nets.get(f"{component.reference}.{pin.number}")
            if net:
                return net
    return None


def _default_voltage(state: ProjectState) -> tuple[str | None, str]:
    if _resolve_rail(state, "3.3V"):
        return "3.3V", "the project only provides a 3.3 V logic rail"
    if _resolve_rail(state, "5V"):
        return "5V", "the project only provides a 5 V rail"
    return None, "no power rail could be identified"


def generate_plan(
    state: ProjectState,
    selected: list[str],
    instruction: str,
    parsed: ParsedInstruction | None = None,
    answers: PlanAnswers | None = None,
) -> ActionPlan | Clarification:
    """Deterministic I2C planner. Never guesses when the schematic is ambiguous."""
    parsed = parsed or parse_instruction(instruction)
    answers = answers or PlanAnswers()
    parsed = replace(
        parsed,
        protocol=answers.protocol or parsed.protocol,
        logic_voltage=answers.logic_voltage or parsed.logic_voltage,
        pullup_value=answers.pullup_value or parsed.pullup_value,
    )

    unknown = [ref for ref in selected if ref not in state.components]
    if unknown:
        return Clarification(
            question=(
                f"Component(s) {', '.join(unknown)} do not exist in this project. "
                "Which components did you mean?"
            ),
            reason="unknown_component",
            options=sorted(ref for ref, c in state.components.items() if not c.is_power),
            answer_key="selection",
        )
    if len(selected) < 2:
        return Clarification(
            question="Select at least two components to connect.",
            reason="insufficient_selection",
            options=sorted(ref for ref, c in state.components.items() if not c.is_power),
            answer_key="selection",
        )

    if parsed.protocol is None:
        return Clarification(
            question="Which interface should be used to connect these components?",
            reason="protocol_not_specified",
            options=["I2C", "SPI", "UART"],
            answer_key="protocol",
        )
    if parsed.protocol not in SUPPORTED_PROTOCOLS:
        return Clarification(
            question=f"{parsed.protocol} is not supported yet. Connect these components using I2C instead?",
            reason="unsupported_protocol",
            options=["I2C"],
            answer_key="protocol",
        )

    controllers = [ref for ref in selected if _is_controller(state.components[ref])]
    peripherals = [ref for ref in selected if ref not in controllers]
    if len(controllers) != 1 or len(peripherals) != 1:
        return Clarification(
            question="Which component is the I2C controller and which is the peripheral?",
            reason="ambiguous_roles",
            options=selected,
            answer_key="selection",
        )
    controller_ref, peripheral_ref = controllers[0], peripherals[0]
    controller, peripheral = state.components[controller_ref], state.components[peripheral_ref]

    assumptions = [f"{controller_ref} is the I2C controller", f"{peripheral_ref} is the I2C peripheral"]
    warnings: list[str] = []

    voltage = parsed.logic_voltage
    if voltage is None:
        voltage, why = _default_voltage(state)
        if voltage is None:
            return Clarification(
                question="Which logic voltage should be used for this connection?",
                reason="voltage_not_specified",
                options=["3.3V", "5V"],
                answer_key="logic_voltage",
            )
        assumptions.append(f"{voltage} logic assumed because {why}")

    rail = _resolve_rail(state, voltage)
    if rail is None:
        available = sorted(v for v in VOLTAGE_RAIL_ALIASES if _resolve_rail(state, v))
        return Clarification(
            question=(
                f"The project has no {voltage} rail, so {peripheral_ref} cannot be powered at {voltage}. "
                f"Use {' or '.join(available) if available else 'another rail'} instead?"
            ),
            reason="incompatible_voltage",
            options=available,
            answer_key="logic_voltage",
        )

    controller_supply = _supply_net(state, controller)
    if controller_supply and controller_supply != rail:
        return Clarification(
            question=(
                f"{controller_ref} is powered from {controller_supply}, so a {voltage} bus would need level "
                f"shifting, which this MVP does not add. Connect the bus at {controller_supply} "
                "logic instead?"
            ),
            reason="incompatible_logic_voltage",
            options=[controller_supply, "Cancel"],
            answer_key="logic_voltage",
        )

    peripheral_sda = _answered_pin(peripheral, answers.peripheral_sda) or _pin_by_names(peripheral, SDA_NAMES)
    peripheral_scl = _answered_pin(peripheral, answers.peripheral_scl) or _pin_by_names(peripheral, SCL_NAMES)
    for label, signal, pin_name in (
        ("SDA", "data", peripheral_sda),
        ("SCL", "clock", peripheral_scl),
    ):
        if pin_name is None:
            return Clarification(
                question=(
                    f"The symbol for {peripheral_ref} does not identify its SDA/SCL pins. "
                    f"Which pin carries I2C {signal} ({label})?"
                ),
                reason="unidentified_peripheral_pins",
                options=[f"{p.number}:{p.name}" for p in peripheral.pins],
                answer_key=f"peripheral_{label.lower()}",
            )

    controller_sda = (
        _answered_pin(controller, answers.controller_sda)
        or parsed.sda_pin
        or _pin_by_names(controller, SDA_NAMES)
    )
    controller_scl = (
        _answered_pin(controller, answers.controller_scl)
        or parsed.scl_pin
        or _pin_by_names(controller, SCL_NAMES)
    )
    if controller_sda is None and controller.pin("GPIO21"):
        controller_sda = "GPIO21"
        assumptions.append("GPIO21 used for SDA (default ESP32 I2C pin)")
    if controller_scl is None and controller.pin("GPIO22"):
        controller_scl = "GPIO22"
        assumptions.append("GPIO22 used for SCL (default ESP32 I2C pin)")
    for label, pin_name in (("SDA", controller_sda), ("SCL", controller_scl)):
        if pin_name is None or controller.pin(pin_name) is None:
            return Clarification(
                question=f"Which {controller_ref} pin should be used for {label}?",
                reason="controller_pin_unresolved",
                options=[p.name for p in controller.pins if CONTROLLER_PIN_HINT.match(p.name)],
                answer_key=f"controller_{label.lower()}",
            )

    peripheral_vcc = _pin_by_names(peripheral, VCC_NAMES)
    peripheral_gnd = _pin_by_names(peripheral, GND_NAMES)
    if peripheral_vcc is None or peripheral_gnd is None:
        warnings.append(f"{peripheral_ref} has no identifiable power or ground pin; power was left unchanged")

    sda_net = state.pin_nets.get(f"{peripheral_ref}.{peripheral.pin(peripheral_sda).number}") or "I2C_SDA"
    scl_net = state.pin_nets.get(f"{peripheral_ref}.{peripheral.pin(peripheral_scl).number}") or "I2C_SCL"
    gnd_net = _ground_net(state)
    pullup_value = parsed.pullup_value or "4.7k"

    actions: list = [
        ConnectPins(
            id="action-1",
            **{"from": f"{peripheral_ref}.{peripheral_sda}", "to": f"{controller_ref}.{controller_sda}"},
            net_name=sda_net,
            purpose="I2C data",
        ),
        ConnectPins(
            id="action-2",
            **{"from": f"{peripheral_ref}.{peripheral_scl}", "to": f"{controller_ref}.{controller_scl}"},
            net_name=scl_net,
            purpose="I2C clock",
        ),
    ]
    if peripheral_vcc:
        actions.append(
            ConnectPinToNet(
                id="action-3", pin=f"{peripheral_ref}.{peripheral_vcc}", net=rail, purpose="Power"
            )
        )
    if peripheral_gnd:
        actions.append(
            ConnectPinToNet(
                id="action-4", pin=f"{peripheral_ref}.{peripheral_gnd}", net=gnd_net, purpose="Ground"
            )
        )

    next_index = len(actions) + 1
    for net in (sda_net, scl_net):
        existing = _existing_pullup(state, net, rail)
        if existing:
            assumptions.append(
                f"{net} already has pull-up {existing[0]} ({existing[1]}) to {rail}; none added"
            )
            continue
        if not parsed.allow_new_components:
            warnings.append(f"{net} has no pull-up to {rail} but adding components was not allowed")
            continue
        actions.append(
            EnsurePullup(
                id=f"action-{next_index}",
                net=net,
                to_net=rail,
                value=pullup_value,
                purpose="Bus pull-up",
            )
        )
        next_index += 1

    protected = sorted(set(parsed.protected_objects))
    return ActionPlan(
        goal=f"Connect {peripheral_ref} to {controller_ref} using I2C with {voltage} logic",
        selected_components=selected,
        protocol=Protocol.I2C,
        logic_voltage=voltage,
        assumptions=assumptions,
        warnings=warnings,
        protected_objects=protected,
        actions=actions,
    )
