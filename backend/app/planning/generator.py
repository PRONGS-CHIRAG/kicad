"""Turn an instruction plus real KiCAD state into a structured action plan.

One algorithm, five protocols. Everything protocol-specific lives in
`protocols.py`; this module resolves pins, refuses to guess, and emits only the
three existing action types.
"""

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
from .protocols import SUPPORTED_PROTOCOLS, ProtocolSpec, SignalSpec, spec_for

CONTROLLER_LIB_HINTS = ("mcu", "esp32", "esp8266", "stm32", "atmega", "rp2040", "microcontroller", "cpu")
CONTROLLER_PIN_HINT = re.compile(r"^(GPIO|IO|P[A-D])\d+$", re.IGNORECASE)
VCC_NAMES = ("VCC", "VDD", "V+", "3V3", "VIN", "VS")
GND_NAMES = ("GND", "VSS", "AGND")

VOLTAGE_RAIL_ALIASES = {
    "3.3V": ("+3V3", "3V3", "+3.3V", "VDD3V3"),
    "5V": ("+5V", "5V"),
    "1.8V": ("+1V8", "1V8"),
}

#: Inclusive ohm range that counts as an acceptable I2C bus pull-up (plan §13).
PULLUP_MIN_OHMS = 1_000.0
PULLUP_MAX_OHMS = 10_000.0

#: "4.7k", "4k7", "10k", "100k", "470", "4.7 kOhm", "470R"
_RESISTOR_VALUE = re.compile(
    r"^\s*(?P<whole>\d+)(?:[.,](?P<frac>\d+))?\s*(?P<mult>[rRkKmM])?(?P<tail>\d*)\s*"
    r"(?:Ω|ω|ohms?|R)?\s*$"
)
_MULTIPLIERS = {"": 1.0, "r": 1.0, "k": 1e3, "m": 1e6}


def parse_resistor_ohms(value: str) -> float | None:
    """Parse a resistor value into ohms, or None when it is not recognisable."""
    if not value:
        return None
    match = _RESISTOR_VALUE.match(value)
    if match is None:
        return None
    mult_token = (match.group("mult") or "").lower()
    multiplier = _MULTIPLIERS.get(mult_token)
    if multiplier is None:
        return None
    tail, frac = match.group("tail"), match.group("frac")
    if tail and frac:
        # "4.7k7" is nonsense; refuse rather than pick an interpretation.
        return None
    if tail:
        # RKM notation: "4k7" == 4.7k.
        digits = f"{match.group('whole')}.{tail}"
    elif frac:
        digits = f"{match.group('whole')}.{frac}"
    else:
        digits = match.group("whole")
    return float(digits) * multiplier


def is_acceptable_pullup(value: str) -> bool | None:
    """True/False if the value is in range; None when it cannot be parsed."""
    ohms = parse_resistor_ohms(value)
    if ohms is None:
        return None
    return PULLUP_MIN_OHMS <= ohms <= PULLUP_MAX_OHMS


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


def _selectable(state: ProjectState) -> list[str]:
    return sorted(ref for ref, c in state.components.items() if not c.is_power)


def _controller_answer_key(signal: str) -> str:
    """Legacy keys for I2C's two signals, namespaced keys for everything else."""
    if signal in ("SDA", "SCL"):
        return f"controller_{signal.lower()}"
    return f"controller_pin:{signal}"


def _peripheral_answer_key(signal: str) -> str:
    if signal in ("SDA", "SCL"):
        return f"peripheral_{signal.lower()}"
    return f"peripheral_pin:{signal}"


def _answer_maps(answers: PlanAnswers) -> tuple[dict[str, str], dict[str, str]]:
    """Fold the legacy scalar answers into the per-signal dicts."""
    controller = dict(answers.controller_pins)
    peripheral = dict(answers.peripheral_pins)
    for signal, legacy in (("SDA", answers.controller_sda), ("SCL", answers.controller_scl)):
        if legacy and signal not in controller:
            controller[signal] = legacy
    for signal, legacy in (("SDA", answers.peripheral_sda), ("SCL", answers.peripheral_scl)):
        if legacy and signal not in peripheral:
            peripheral[signal] = legacy
    return controller, peripheral


def _signal_net(
    state: ProjectState,
    spec: ProtocolSpec,
    signal: SignalSpec,
    peripheral_ref: str,
    peripheral_pin_number: str,
    controller_pin: str,
) -> str:
    existing = state.pin_nets.get(f"{peripheral_ref}.{peripheral_pin_number}")
    if existing:
        return existing
    if spec.protocol is Protocol.GPIO:
        # A bare GPIO connection has no bus to name it after; §8 derives the net
        # from the controller pin instead of the protocol prefix.
        return f"NET_{controller_pin.upper()}"
    return f"{spec.net_prefix}_{signal.name}"


def _power_pins(
    peripheral: Component, peripheral_answers: dict[str, str]
) -> tuple[str | None, str | None]:
    vcc = _answered_pin(peripheral, peripheral_answers.get("VCC")) or _pin_by_names(
        peripheral, VCC_NAMES
    )
    gnd = _answered_pin(peripheral, peripheral_answers.get("GND")) or _pin_by_names(
        peripheral, GND_NAMES
    )
    return vcc, gnd


def generate_plan(
    state: ProjectState,
    selected: list[str],
    instruction: str,
    parsed: ParsedInstruction | None = None,
    answers: PlanAnswers | None = None,
) -> ActionPlan | Clarification:
    """Deterministic protocol-driven planner. Never guesses when the schematic is ambiguous."""
    parsed = parsed or parse_instruction(instruction)
    answers = answers or PlanAnswers()
    parsed = replace(
        parsed,
        protocol=answers.protocol or parsed.protocol,
        logic_voltage=answers.logic_voltage or parsed.logic_voltage,
        pullup_value=answers.pullup_value or parsed.pullup_value,
    )
    controller_answers, peripheral_answers = _answer_maps(answers)

    unknown = [ref for ref in selected if ref not in state.components]
    if unknown:
        return Clarification(
            question=(
                f"Component(s) {', '.join(unknown)} do not exist in this project. "
                "Which components did you mean?"
            ),
            reason="unknown_component",
            options=_selectable(state),
            answer_key="selection",
        )

    if parsed.protocol is None:
        return Clarification(
            question="Which interface should be used to connect these components?",
            reason="protocol_not_specified",
            options=["I2C", "SPI", "UART", "GPIO", "POWER"],
            answer_key="protocol",
        )
    spec = spec_for(parsed.protocol)
    if spec is None:
        return Clarification(
            question=f"{parsed.protocol} is not supported yet. Connect these components using I2C instead?",
            reason="unsupported_protocol",
            options=sorted(SUPPORTED_PROTOCOLS),
            answer_key="protocol",
        )
    protocol = spec.protocol

    # POWER acts on one component, so it is the only protocol for which a single
    # selection is meaningful. Every other protocol still needs two endpoints.
    minimum = 1 if protocol is Protocol.POWER else 2
    if len(selected) < minimum:
        return Clarification(
            question=f"Select at least {'one component' if minimum == 1 else 'two components'} to connect.",
            reason="insufficient_selection",
            options=_selectable(state),
            answer_key="selection",
        )

    assumptions: list[str] = []
    warnings: list[str] = []
    controller_ref: str | None = None
    controller: Component | None = None

    if protocol is Protocol.POWER:
        candidates = [ref for ref in selected if not state.components[ref].is_power]
        if len(candidates) != 1:
            return Clarification(
                question="Which component should be powered?",
                reason="ambiguous_roles",
                options=candidates or selected,
                answer_key="selection",
            )
        peripheral_ref = candidates[0]
        assumptions.append(f"{peripheral_ref} is the component to power")
    else:
        controllers = [ref for ref in selected if _is_controller(state.components[ref])]
        peripherals = [ref for ref in selected if ref not in controllers]
        if len(controllers) != 1 or len(peripherals) != 1:
            return Clarification(
                question=(
                    f"Which component is the {protocol.value} controller and which is the peripheral?"
                ),
                reason="ambiguous_roles",
                options=selected,
                answer_key="selection",
            )
        controller_ref, peripheral_ref = controllers[0], peripherals[0]
        controller = state.components[controller_ref]
        assumptions.append(f"{controller_ref} is the {protocol.value} controller")
        assumptions.append(f"{peripheral_ref} is the {protocol.value} peripheral")
    peripheral = state.components[peripheral_ref]

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

    if controller is not None:
        controller_supply = _supply_net(state, controller)
        if controller_supply and controller_supply != rail:
            return Clarification(
                question=(
                    f"{controller_ref} is powered from {controller_supply}, so a {voltage} bus "
                    f"would need level "
                    f"shifting, which this MVP does not add. Connect the bus at {controller_supply} "
                    "logic instead?"
                ),
                reason="incompatible_logic_voltage",
                options=[controller_supply, "Cancel"],
                answer_key="logic_voltage",
            )

    # Peripheral side first, one question at a time, then the controller side.
    peripheral_pins: dict[str, str] = {}
    for signal in spec.signals:
        pin_name = _answered_pin(peripheral, peripheral_answers.get(signal.name)) or _pin_by_names(
            peripheral, signal.peripheral_names
        )
        if pin_name is None:
            return Clarification(
                question=(
                    f"The symbol for {peripheral_ref} does not identify its {protocol.value} pins. "
                    f"Which pin carries {signal.purpose} ({signal.name})?"
                ),
                reason="unidentified_peripheral_pins",
                options=[f"{p.number}:{p.name}" for p in peripheral.pins],
                answer_key=_peripheral_answer_key(signal.name),
            )
        peripheral_pins[signal.name] = pin_name

    controller_pins: dict[str, str] = {}
    for signal in spec.signals:
        assert controller is not None and controller_ref is not None  # POWER has no signals
        pin_name = (
            _answered_pin(controller, controller_answers.get(signal.name))
            or parsed.signal_pins.get(signal.name)
            or _pin_by_names(controller, signal.controller_names)
        )
        if (
            pin_name is None
            and signal.default_controller_pin
            and controller.pin(signal.default_controller_pin)
        ):
            pin_name = signal.default_controller_pin
            assumptions.append(
                f"{pin_name} used for {signal.name} (default ESP32 {protocol.value} pin)"
            )
        if pin_name is None or controller.pin(pin_name) is None:
            return Clarification(
                question=f"Which {controller_ref} pin should be used for {signal.name}?",
                reason="controller_pin_unresolved",
                options=[p.name for p in controller.pins if CONTROLLER_PIN_HINT.match(p.name)],
                answer_key=_controller_answer_key(signal.name),
            )
        controller_pins[signal.name] = pin_name

    peripheral_vcc, peripheral_gnd = _power_pins(peripheral, peripheral_answers)
    if peripheral_vcc is None or peripheral_gnd is None:
        if protocol is Protocol.POWER:
            # A POWER plan is nothing but power and ground: without them there is
            # no plan at all, and an empty plan is the wrong-shaped failure.
            missing = "power (VCC)" if peripheral_vcc is None else "ground (GND)"
            return Clarification(
                question=(
                    f"The symbol for {peripheral_ref} does not identify its {missing} pin. "
                    "Which pin is it?"
                ),
                reason="unidentified_power_pins",
                options=[f"{p.number}:{p.name}" for p in peripheral.pins],
                answer_key=_peripheral_answer_key("VCC" if peripheral_vcc is None else "GND"),
            )
        warnings.append(f"{peripheral_ref} has no identifiable power or ground pin; power was left unchanged")

    gnd_net = _ground_net(state)
    pullup_value = parsed.pullup_value or "4.7k"
    actions: list = []
    signal_nets: dict[str, str] = {}

    for signal in spec.signals:
        peripheral_pin = peripheral_pins[signal.name]
        controller_pin = controller_pins[signal.name]
        net = _signal_net(
            state,
            spec,
            signal,
            peripheral_ref,
            peripheral.pin(peripheral_pin).number,
            controller_pin,
        )
        signal_nets[signal.name] = net
        actions.append(
            ConnectPins(
                id=f"action-{len(actions) + 1}",
                **{
                    "from": f"{peripheral_ref}.{peripheral_pin}",
                    "to": f"{controller_ref}.{controller_pin}",
                },
                net_name=net,
                purpose=signal.purpose,
            )
        )

    if peripheral_vcc:
        actions.append(
            ConnectPinToNet(
                id=f"action-{len(actions) + 1}",
                pin=f"{peripheral_ref}.{peripheral_vcc}",
                net=rail,
                purpose="Power",
            )
        )
    if peripheral_gnd:
        actions.append(
            ConnectPinToNet(
                id=f"action-{len(actions) + 1}",
                pin=f"{peripheral_ref}.{peripheral_gnd}",
                net=gnd_net,
                purpose="Ground",
            )
        )

    for signal_name in spec.pullup_signals:
        net = signal_nets.get(signal_name)
        if net is None:
            continue
        existing = _existing_pullup(state, net, rail)
        out_of_range: tuple[str, str] | None = None
        if existing:
            reference, value = existing
            acceptable = is_acceptable_pullup(value)
            if acceptable is not False:
                if acceptable is None:
                    warnings.append(
                        f"{net} already has pull-up {reference} whose value {value!r} could not be "
                        "read; it was left in place and no resistor was added"
                    )
                assumptions.append(
                    f"{net} already has pull-up {reference} ({value}) to {rail}; none added"
                )
                continue
            out_of_range = existing
        if not parsed.allow_new_components:
            if out_of_range:
                warnings.append(
                    f"{net} pull-up {out_of_range[0]} ({out_of_range[1]}) is outside the 1k-10k "
                    "range but adding components was not allowed"
                )
            else:
                warnings.append(
                    f"{net} has no pull-up to {rail} but adding components was not allowed"
                )
            continue
        if out_of_range:
            # §13: never silently replace or delete their part; add a correct one.
            warnings.append(
                f"{net} already has pull-up {out_of_range[0]} ({out_of_range[1]}) to {rail}, which "
                f"is outside the 1k-10k range for an I2C pull-up; {out_of_range[0]} was left "
                f"unchanged and a {pullup_value} pull-up was added alongside it"
            )
        actions.append(
            EnsurePullup(
                id=f"action-{len(actions) + 1}",
                net=net,
                to_net=rail,
                value=pullup_value,
                purpose="Bus pull-up",
            )
        )

    if protocol is Protocol.POWER:
        goal = f"Power {peripheral_ref} from {rail} and {gnd_net}"
    else:
        goal = f"Connect {peripheral_ref} to {controller_ref} using {protocol.value} with {voltage} logic"

    protected = sorted(set(parsed.protected_objects))
    return ActionPlan(
        goal=goal,
        selected_components=selected,
        protocol=protocol,
        logic_voltage=voltage,
        assumptions=assumptions,
        warnings=warnings,
        protected_objects=protected,
        actions=actions,
    )
