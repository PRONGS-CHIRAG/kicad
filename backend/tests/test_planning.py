from __future__ import annotations

from app.models import ActionPlan, Clarification, ConnectPins, ConnectPinToNet, EnsurePullup
from app.planning.generator import generate_plan
from app.planning.instruction import parse_instruction
from app.planning.validator import validate_plan

DEMO_INSTRUCTION = (
    "Connect U1 and U2 using I2C.\n"
    "Use 3.3 V logic.\n"
    "Use GPIO21 for SDA and GPIO22 for SCL.\n"
    "Add 4.7 kΩ pull-ups if they are missing.\n"
    "Do not modify J1 or the USB circuit."
)


def test_instruction_parsing() -> None:
    parsed = parse_instruction(DEMO_INSTRUCTION)
    assert parsed.protocol == "I2C"
    assert parsed.logic_voltage == "3.3V"
    assert parsed.pullup_value == "4.7k"
    assert (parsed.sda_pin, parsed.scl_pin) == ("GPIO21", "GPIO22")
    assert parsed.protected_objects == ["J1", "USB_D+", "USB_D-", "VBUS"]


def test_plan_for_demo_instruction(state) -> None:
    plan = generate_plan(state, ["U1", "U2"], DEMO_INSTRUCTION)
    assert isinstance(plan, ActionPlan)
    assert [a.type.value for a in plan.actions] == [
        "connect_pins",
        "connect_pins",
        "connect_pin_to_net",
        "connect_pin_to_net",
        "ensure_pullup",
        "ensure_pullup",
    ]
    connect = [a for a in plan.actions if isinstance(a, ConnectPins)]
    assert (connect[0].from_pin, connect[0].to_pin, connect[0].net_name) == ("U2.SDA", "U1.GPIO21", "I2C_SDA")
    power = [a for a in plan.actions if isinstance(a, ConnectPinToNet)]
    assert {a.net for a in power} == {"+3V3", "GND"}
    assert all(a.value == "4.7k" for a in plan.actions if isinstance(a, EnsurePullup))
    assert plan.protected_objects == ["J1", "USB_D+", "USB_D-", "VBUS"]
    assert validate_plan(state, plan) == []


def test_missing_protocol_asks_for_clarification(state) -> None:
    result = generate_plan(state, ["U1", "U2"], "Connect the sensor properly.")
    assert isinstance(result, Clarification)
    assert result.reason == "protocol_not_specified"


def test_unsupported_protocol_is_refused(state) -> None:
    result = generate_plan(state, ["U1", "U2"], "Connect U1 and U2 over SPI at 3.3V.")
    assert isinstance(result, Clarification)
    assert result.reason == "unsupported_protocol"


def test_unknown_component_is_refused_before_execution(state) -> None:
    result = generate_plan(state, ["U1", "U99"], "Connect U1 and U99 using I2C with 3.3V logic.")
    assert isinstance(result, Clarification)
    assert result.reason == "unknown_component"


def test_incompatible_voltage_is_refused(state) -> None:
    result = generate_plan(state, ["U1", "U2"], "Connect U1 and U2 using I2C with 5V logic.")
    assert isinstance(result, Clarification)
    assert result.reason == "incompatible_voltage"


def test_existing_pullups_are_not_duplicated(store) -> None:
    session = store.create("esp32_i2c_existing_pullups")
    plan, problems, _ = store.plan(
        session, ["U1", "U2"], "Connect U1 and U2 using I2C with 3.3V logic. Add pull-ups if missing."
    )
    assert isinstance(plan, ActionPlan) and problems == []
    assert not [a for a in plan.actions if isinstance(a, EnsurePullup)]
    assert any("already has pull-up" in assumption for assumption in plan.assumptions)


def test_unidentified_peripheral_pins_are_not_guessed(store) -> None:
    session = store.create("esp32_i2c_unnamed_pins")
    result, _, _ = store.plan(session, ["U1", "U2"], "Connect U1 and U2 using I2C with 3.3V logic.")
    assert isinstance(result, Clarification)
    assert result.reason == "unidentified_peripheral_pins"


def test_validator_rejects_nonexistent_pin(state) -> None:
    plan = ActionPlan(
        goal="bad",
        selected_components=["U1", "U2"],
        actions=[ConnectPins(id="a1", **{"from": "U2.SDA", "to": "U1.GPIO99"}, net_name="I2C_SDA")],
    )
    assert validate_plan(state, plan) == ["a1: pin GPIO99 does not exist on U1"]


def test_validator_rejects_ground_to_power(state) -> None:
    plan = ActionPlan(
        goal="bad",
        selected_components=["U1", "U2"],
        actions=[ConnectPinToNet(id="a1", pin="U2.GND", net="+3V3")],
    )
    assert validate_plan(state, plan) == ["a1: ground pin U2.GND would be tied to power net +3V3"]


def test_validator_rejects_shared_sda_scl_net(state) -> None:
    plan = ActionPlan(
        goal="bad",
        selected_components=["U1", "U2"],
        actions=[
            ConnectPins(id="a1", **{"from": "U2.SDA", "to": "U1.GPIO21"}, net_name="I2C_BUS"),
            ConnectPins(id="a2", **{"from": "U2.SCL", "to": "U1.GPIO22"}, net_name="I2C_BUS"),
        ],
    )
    assert "SDA and SCL must not share the same net" in validate_plan(state, plan)


def test_validator_rejects_touching_protected_objects(state) -> None:
    plan = ActionPlan(
        goal="bad",
        selected_components=["U1", "J1"],
        protected_objects=["J1"],
        actions=[ConnectPins(id="a1", **{"from": "J1.D+", "to": "U1.GPIO21"}, net_name="I2C_SDA")],
    )
    assert "a1: J1.D+ is protected and must not change" in validate_plan(state, plan)


def test_validator_rejects_pullup_to_ground(state) -> None:
    plan = ActionPlan(
        goal="bad",
        selected_components=["U1", "U2"],
        actions=[EnsurePullup(id="a1", net="I2C_SDA", to_net="GND")],
    )
    assert "a1: pull-up to ground is not valid" in validate_plan(state, plan)
