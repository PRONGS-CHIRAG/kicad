"""Planning-level coverage for the non-I2C protocols added in §7.

Each protocol is exercised against the fixture built for it, end to end from the
free-text instruction through `generate_plan` to `validate_plan`. The point is
not that a plan appears, but that the *right* plan appears: the correct number of
connections, the correct direction, distinct nets, and pull-ups only on I2C.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from app.kicad.reader import ProjectState, read_project
from app.models import (
    ActionPlan,
    Clarification,
    ConnectPins,
    ConnectPinToNet,
    EnsurePullup,
    PlanAnswers,
    Protocol,
)
from app.planning.generator import generate_plan, is_acceptable_pullup, parse_resistor_ohms
from app.planning.instruction import parse_instruction
from app.planning.protocols import PROTOCOL_SPECS
from app.planning.validator import validate_plan

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "projects"


def load(tmp_path: Path, name: str) -> ProjectState:
    """A private copy of a fixture, so nothing a test does can touch the repo."""
    target = tmp_path / name
    shutil.copytree(FIXTURES / name, target)
    return read_project(target)


@pytest.fixture()
def spi_state(tmp_path: Path) -> ProjectState:
    return load(tmp_path, "esp32_spi_display")


@pytest.fixture()
def uart_state(tmp_path: Path) -> ProjectState:
    return load(tmp_path, "esp32_uart_module")


@pytest.fixture()
def gpio_state(tmp_path: Path) -> ProjectState:
    return load(tmp_path, "esp32_gpio_peripheral")


@pytest.fixture()
def power_state(tmp_path: Path) -> ProjectState:
    return load(tmp_path, "esp32_power_only")


def pairs(plan: ActionPlan) -> set[tuple[str, str]]:
    return {(a.from_pin, a.to_pin) for a in plan.actions if isinstance(a, ConnectPins)}


# --------------------------------------------------------------------------- SPI


def test_spi_plan_connects_all_four_signals_with_power_and_no_pullups(spi_state) -> None:
    plan = generate_plan(spi_state, ["U1", "U2"], "Connect U1 and U2 over SPI at 3.3V.")
    assert isinstance(plan, Clarification) is False, getattr(plan, "question", "")
    assert isinstance(plan, ActionPlan)
    assert plan.protocol is Protocol.SPI

    assert [a.type.value for a in plan.actions] == [
        "connect_pins",
        "connect_pins",
        "connect_pins",
        "connect_pins",
        "connect_pin_to_net",
        "connect_pin_to_net",
    ]
    # from = peripheral, to = controller, matching the I2C convention.
    assert pairs(plan) == {
        ("U2.SCK", "U1.SCK"),
        ("U2.MOSI", "U1.MOSI"),
        ("U2.MISO", "U1.MISO"),
        ("U2.CS", "U1.CS"),
    }
    nets = [a.net_name for a in plan.actions if isinstance(a, ConnectPins)]
    assert sorted(nets) == ["SPI_CS", "SPI_MISO", "SPI_MOSI", "SPI_SCK"]
    assert len(set(nets)) == 4, "each SPI signal needs its own net"
    assert {a.net for a in plan.actions if isinstance(a, ConnectPinToNet)} == {"+3V3", "GND"}
    assert validate_plan(spi_state, plan) == []


def test_spi_never_gets_pullups(spi_state) -> None:
    plan = generate_plan(spi_state, ["U1", "U2"], "Connect U1 and U2 over SPI at 3.3V.")
    assert isinstance(plan, ActionPlan)
    assert not [a for a in plan.actions if isinstance(a, EnsurePullup)]
    assert PROTOCOL_SPECS["SPI"].pullup_signals == ()


def test_spi_controller_output_driving_a_peripheral_input_is_accepted(spi_state) -> None:
    """The positive half of the §6 output rule: without it, a reversed rule still passes."""
    plan = ActionPlan(
        goal="ok",
        selected_components=["U1", "U2"],
        protocol=Protocol.SPI,
        actions=[
            # U1.SCK is an output, U2.SCK an input. Legal on SPI, illegal on I2C.
            ConnectPins(id="a1", **{"from": "U2.SCK", "to": "U1.SCK"}, net_name="SPI_SCK"),
        ],
    )
    assert validate_plan(spi_state, plan) == []

    same_plan_as_i2c = plan.model_copy(update={"protocol": Protocol.I2C})
    assert validate_plan(spi_state, same_plan_as_i2c) == [
        "a1: U1.SCK is an output and cannot be shared on an I2C bus"
    ]


def test_two_outputs_are_rejected_even_on_spi(spi_state) -> None:
    plan = ActionPlan(
        goal="bad",
        selected_components=["U1", "U2"],
        protocol=Protocol.SPI,
        actions=[
            # U1.TX is an output and so is U2.MISO: nothing may drive both.
            ConnectPins(id="a1", **{"from": "U2.MISO", "to": "U1.TX"}, net_name="SPI_MISO"),
        ],
    )
    assert validate_plan(spi_state, plan) == [
        "a1: U2.MISO and U1.TX are both outputs and cannot be connected"
    ]


def test_two_outputs_are_rejected_on_gpio_too(gpio_state) -> None:
    plan = ActionPlan(
        goal="bad",
        selected_components=["U1", "U2"],
        protocol=Protocol.GPIO,
        actions=[ConnectPins(id="a1", **{"from": "U2.FAULT", "to": "U1.TX"}, net_name="NET_TX")],
    )
    assert validate_plan(gpio_state, plan) == [
        "a1: U2.FAULT and U1.TX are both outputs and cannot be connected"
    ]


# -------------------------------------------------------------------------- UART


def test_uart_crosses_tx_and_rx_over_two_distinct_nets(uart_state) -> None:
    plan = generate_plan(uart_state, ["U1", "U2"], "Connect U1 and U2 over UART at 3.3V.")
    assert isinstance(plan, ActionPlan)
    assert plan.protocol is Protocol.UART

    # The controller's TX reaches the peripheral's RX and vice versa. `from` is
    # the peripheral end, so the pairs read (peripheral, controller).
    assert pairs(plan) == {("U2.RX", "U1.TX"), ("U2.TX", "U1.RX")}

    by_net = {a.net_name: (a.from_pin, a.to_pin) for a in plan.actions if isinstance(a, ConnectPins)}
    assert by_net["UART_TX"] == ("U2.RX", "U1.TX")
    assert by_net["UART_RX"] == ("U2.TX", "U1.RX")
    assert len(by_net) == 2, "TX and RX must not share a net"

    assert {a.net for a in plan.actions if isinstance(a, ConnectPinToNet)} == {"+3V3", "GND"}
    assert not [a for a in plan.actions if isinstance(a, EnsurePullup)]
    assert PROTOCOL_SPECS["UART"].pullup_signals == ()
    assert validate_plan(uart_state, plan) == []


def test_uart_crossover_is_alias_driven_not_special_cased() -> None:
    tx = PROTOCOL_SPECS["UART"].signal("TX")
    rx = PROTOCOL_SPECS["UART"].signal("RX")
    assert tx is not None and rx is not None
    assert tx.controller_names[0] == "TX" and tx.peripheral_names[0] == "RX"
    assert rx.controller_names[0] == "RX" and rx.peripheral_names[0] == "TX"


# -------------------------------------------------------------------------- GPIO


def test_gpio_refuses_to_guess_the_peripheral_pin(gpio_state) -> None:
    result = generate_plan(gpio_state, ["U1", "U2"], "Connect U1 to U2 with a plain GPIO line at 3.3V.")
    assert isinstance(result, Clarification)
    assert result.reason == "unidentified_peripheral_pins"
    assert result.answer_key == "peripheral_pin:GPIO"
    assert "3:IN1" in result.options


def test_gpio_refuses_to_guess_the_controller_pin(gpio_state) -> None:
    """Even with the peripheral end answered, the controller end is never inferred."""
    result = generate_plan(
        gpio_state,
        ["U1", "U2"],
        "Connect U1 to U2 with a plain GPIO line at 3.3V.",
        answers=PlanAnswers(peripheral_pins={"GPIO": "3:IN1"}),
    )
    assert isinstance(result, Clarification)
    assert result.reason == "controller_pin_unresolved"
    assert result.answer_key == "controller_pin:GPIO"
    assert "GPIO25" in result.options


def test_gpio_plan_names_the_net_after_the_controller_pin(gpio_state) -> None:
    plan = generate_plan(
        gpio_state,
        ["U1", "U2"],
        "Connect U1 to U2 with a plain GPIO line at 3.3V.",
        answers=PlanAnswers(
            peripheral_pins={"GPIO": "3:IN1"}, controller_pins={"GPIO": "GPIO25"}
        ),
    )
    assert isinstance(plan, ActionPlan)
    assert plan.protocol is Protocol.GPIO
    connect = [a for a in plan.actions if isinstance(a, ConnectPins)]
    assert len(connect) == 1
    assert (connect[0].from_pin, connect[0].to_pin, connect[0].net_name) == (
        "U2.IN1",
        "U1.GPIO25",
        "NET_GPIO25",
    )
    assert not [a for a in plan.actions if isinstance(a, EnsurePullup)]
    assert validate_plan(gpio_state, plan) == []


# ------------------------------------------------------------------------- POWER


def test_power_plan_on_a_single_selected_component(power_state) -> None:
    plan = generate_plan(power_state, ["U2"], "Power the fan from the 3.3V rail.")
    assert isinstance(plan, ActionPlan)
    assert plan.protocol is Protocol.POWER
    assert [a.type.value for a in plan.actions] == ["connect_pin_to_net", "connect_pin_to_net"]
    assert [(a.pin, a.net) for a in plan.actions] == [("U2.VCC", "+3V3"), ("U2.GND", "GND")]
    assert not [a for a in plan.actions if isinstance(a, EnsurePullup)]
    assert validate_plan(power_state, plan) == []


def test_power_never_produces_a_zero_action_plan(power_state) -> None:
    """§8: an unidentifiable rail pin must clarify, not yield an empty plan."""
    stripped = copy.deepcopy(power_state)
    fan = stripped.components["U2"]
    fan.pins = [p for p in fan.pins if p.name != "VCC"]
    result = generate_plan(stripped, ["U2"], "Power the fan from the 3.3V rail.")
    assert isinstance(result, Clarification)
    assert result.reason == "unidentified_power_pins"
    assert result.answer_key == "peripheral_pin:VCC"


def test_power_with_two_candidates_asks_which_one(power_state) -> None:
    result = generate_plan(power_state, ["U1", "U2"], "Power the fan from the 3.3V rail.")
    assert isinstance(result, Clarification)
    assert result.reason == "ambiguous_roles"
    assert result.answer_key == "selection"


def test_other_protocols_still_need_two_components(power_state) -> None:
    result = generate_plan(power_state, ["U2"], "Connect U1 and U2 over SPI at 3.3V.")
    assert isinstance(result, Clarification)
    assert result.reason == "insufficient_selection"


# ------------------------------------------------------- protocol detection (§9)


def test_protocol_detection_order() -> None:
    assert parse_instruction("Connect U1 and U2 using I2C on GPIO21/GPIO22.").protocol == "I2C"
    assert parse_instruction("Connect over SPI.").protocol == "SPI"
    assert parse_instruction("Wire the UART link.").protocol == "UART"
    assert parse_instruction("Use a plain GPIO line.").protocol == "GPIO"
    assert parse_instruction("Power the module.").protocol == "POWER"


def test_a_bare_pin_token_is_not_a_gpio_protocol_request() -> None:
    """"GPIO21" names a pin. Only a bare `gpio` word asks for a GPIO connection."""
    assert parse_instruction("Connect U1.GPIO21 to U2.IN1.").protocol is None
    assert parse_instruction("Use GPIO21 for SDA and GPIO22 for SCL.").protocol is None
    # ...and when a real protocol IS named alongside pin tokens, it wins.
    assert parse_instruction("Use I2C on GPIO21 and GPIO22.").protocol == "I2C"


def test_power_does_not_fire_on_an_incidental_power_word() -> None:
    golden = (
        "Connect these components using I2C with 3.3 V logic.\n"
        "Add the required pull-up resistors.\n"
        "Do not modify the USB circuit."
    )
    assert parse_instruction(golden).protocol == "I2C"
    assert parse_instruction("Add pull-up resistors to the power net.").protocol is None
    assert parse_instruction("Check the power consumption.").protocol is None


def test_unsupported_protocol_branch_is_still_reachable(spi_state) -> None:
    result = generate_plan(
        spi_state, ["U1", "U2"], "Connect U1 and U2.", answers=PlanAnswers(protocol="CAN")
    )
    assert isinstance(result, Clarification)
    assert result.reason == "unsupported_protocol"


# --------------------------------------------------- §12 SCK/SCL alias collision


def test_i2c_scl_never_resolves_to_the_mcu_sck_output(spi_state) -> None:
    """The MCU exposes an `SCK` output. An I2C SCL request must not land on it."""
    assert "SCK" not in PROTOCOL_SPECS["I2C"].signal("SCL").controller_names
    plan = generate_plan(
        spi_state,
        ["U1", "U2"],
        "Connect U1 and U2 using I2C with 3.3V logic.",
        answers=PlanAnswers(peripheral_sda="4:MOSI", peripheral_scl="3:SCK"),
    )
    assert isinstance(plan, ActionPlan)
    scl = [a for a in plan.actions if isinstance(a, ConnectPins) and a.net_name == "I2C_SCL"]
    assert scl and scl[0].to_pin == "U1.GPIO22"
    assert validate_plan(spi_state, plan) == []


# ------------------------------------------------------ §13 pull-up value checks


@pytest.mark.parametrize(
    ("value", "ohms"),
    [("4.7k", 4700.0), ("4k7", 4700.0), ("10k", 10000.0), ("100k", 100000.0), ("470", 470.0)],
)
def test_resistor_values_parse(value: str, ohms: float) -> None:
    assert parse_resistor_ohms(value) == ohms


def test_pullup_acceptability_range() -> None:
    assert is_acceptable_pullup("1k") is True
    assert is_acceptable_pullup("4.7k") is True
    assert is_acceptable_pullup("10k") is True
    assert is_acceptable_pullup("470") is False
    assert is_acceptable_pullup("100k") is False
    assert is_acceptable_pullup("DNP") is None


def _pullup_project(tmp_path: Path, value: str) -> ProjectState:
    target = tmp_path / "bad_pullups"
    shutil.copytree(FIXTURES / "esp32_i2c_existing_pullups", target)
    for schematic in target.glob("*.kicad_sch"):
        schematic.write_text(schematic.read_text().replace('"4.7k"', f'"{value}"'))
    return read_project(target)


@pytest.mark.parametrize("value", ["100k", "470"])
def test_out_of_range_pullup_warns_and_adds_a_correct_one(tmp_path: Path, value: str) -> None:
    state = _pullup_project(tmp_path, value)
    plan = generate_plan(
        state, ["U1", "U2"], "Connect U1 and U2 using I2C with 3.3V logic. Add 4.7k pull-ups."
    )
    assert isinstance(plan, ActionPlan)

    added = [a for a in plan.actions if isinstance(a, EnsurePullup)]
    assert len(added) == 2, "an unacceptable pull-up still means an acceptable one is missing"
    assert {a.net for a in added} == {"I2C_SDA", "I2C_SCL"}
    assert all(a.value == "4.7k" for a in added)

    # The existing part is named, and left alone.
    assert any(f"R1 ({value})" in w for w in plan.warnings), plan.warnings
    assert any(f"R2 ({value})" in w for w in plan.warnings), plan.warnings
    assert not any("already has pull-up" in a for a in plan.assumptions)
    assert validate_plan(state, plan) == []


def test_in_range_pullup_is_left_alone(tmp_path: Path) -> None:
    state = _pullup_project(tmp_path, "2.2k")
    plan = generate_plan(state, ["U1", "U2"], "Connect U1 and U2 using I2C with 3.3V logic.")
    assert isinstance(plan, ActionPlan)
    assert not [a for a in plan.actions if isinstance(a, EnsurePullup)]
    assert sum("already has pull-up" in a for a in plan.assumptions) == 2
    assert plan.warnings == []


def test_out_of_range_pullup_is_not_added_when_new_components_are_forbidden(tmp_path: Path) -> None:
    state = _pullup_project(tmp_path, "100k")
    plan = generate_plan(
        state,
        ["U1", "U2"],
        "Connect U1 and U2 using I2C with 3.3V logic. Do not add any new components.",
    )
    assert isinstance(plan, ActionPlan)
    assert not [a for a in plan.actions if isinstance(a, EnsurePullup)]
    assert any("outside the 1k-10k range" in w for w in plan.warnings), plan.warnings


# ------------------------------------------------- answer keys stay backwards-compatible


def test_legacy_i2c_answer_keys_are_unchanged(spi_state) -> None:
    i2c = PROTOCOL_SPECS["I2C"]
    assert [s.name for s in i2c.signals] == ["SDA", "SCL"]
    result = generate_plan(spi_state, ["U1", "U2"], "Connect U1 and U2 using I2C with 3.3V logic.")
    assert isinstance(result, Clarification)
    assert result.answer_key == "peripheral_sda"


def test_legacy_scalar_answers_are_folded_into_the_dicts(uart_state) -> None:
    """A client that only knows `controller_sda` still steers the I2C planner."""
    plan = generate_plan(
        uart_state,
        ["U1", "U2"],
        "Connect U1 and U2 using I2C with 3.3V logic.",
        answers=PlanAnswers(
            controller_sda="GPIO18",
            controller_scl="GPIO19",
            peripheral_sda="3:TX",
            peripheral_scl="4:RX",
        ),
    )
    assert isinstance(plan, ActionPlan)
    assert pairs(plan) == {("U2.TX", "U1.GPIO18"), ("U2.RX", "U1.GPIO19")}
