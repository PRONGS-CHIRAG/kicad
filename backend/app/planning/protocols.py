"""Per-protocol signal tables that drive the planner.

The planner is one algorithm; a protocol is data. Each :class:`SignalSpec` says
what a signal is called on the *controller* and on the *peripheral* separately.
That per-side split is what expresses UART's TX/RX crossover without any special
case, and it is what stops an I2C ``SCL`` request from resolving to an MCU's
``SCK`` output (which is SPI's clock, not I2C's).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Protocol


@dataclass(frozen=True)
class SignalSpec:
    name: str
    controller_names: tuple[str, ...]
    peripheral_names: tuple[str, ...]
    purpose: str
    default_controller_pin: str | None = None


@dataclass(frozen=True)
class ProtocolSpec:
    protocol: Protocol
    net_prefix: str
    signals: tuple[SignalSpec, ...]
    pullup_signals: tuple[str, ...] = ()
    shared_outputs_forbidden: bool = True

    def signal(self, name: str) -> SignalSpec | None:
        for signal in self.signals:
            if signal.name == name:
                return signal
        return None


I2C_SPEC = ProtocolSpec(
    protocol=Protocol.I2C,
    net_prefix="I2C",
    signals=(
        SignalSpec(
            name="SDA",
            controller_names=("SDA", "SDIO", "I2C_SDA", "DATA"),
            peripheral_names=("SDA", "SDIO", "I2C_SDA", "DATA"),
            purpose="I2C data",
            default_controller_pin="GPIO21",
        ),
        # `SCK` is deliberately absent from the controller aliases: on an MCU
        # symbol that pin is the SPI clock and an output. Peripherals that really
        # do label their I2C clock SCK are still matched, last.
        SignalSpec(
            name="SCL",
            controller_names=("SCL", "I2C_SCL"),
            peripheral_names=("SCL", "I2C_SCL", "CLK", "SCK"),
            purpose="I2C clock",
            default_controller_pin="GPIO22",
        ),
    ),
    pullup_signals=("SDA", "SCL"),
    shared_outputs_forbidden=True,
)

SPI_SPEC = ProtocolSpec(
    protocol=Protocol.SPI,
    net_prefix="SPI",
    signals=(
        SignalSpec(
            name="SCK",
            controller_names=("SCK", "SCLK", "CLK"),
            peripheral_names=("SCK", "SCLK", "CLK"),
            purpose="SPI clock",
        ),
        SignalSpec(
            name="MOSI",
            controller_names=("MOSI", "SDO", "DOUT"),
            peripheral_names=("MOSI", "SDI", "DIN"),
            purpose="SPI controller-out peripheral-in",
        ),
        SignalSpec(
            name="MISO",
            controller_names=("MISO", "SDI", "DIN"),
            peripheral_names=("MISO", "SDO", "DOUT"),
            purpose="SPI peripheral-out controller-in",
        ),
        SignalSpec(
            name="CS",
            controller_names=("CS", "NSS", "CSN"),
            peripheral_names=("CS", "NCS", "CSN", "CE"),
            purpose="SPI chip select",
        ),
    ),
    shared_outputs_forbidden=False,
)

# The crossover lives entirely in the alias tuples: the controller's TX pin is
# wired to whatever the peripheral calls RX, and vice versa.
UART_SPEC = ProtocolSpec(
    protocol=Protocol.UART,
    net_prefix="UART",
    signals=(
        SignalSpec(
            name="TX",
            controller_names=("TX", "TXD", "UART_TX", "TXO"),
            peripheral_names=("RX", "RXD", "UART_RX", "RXI"),
            purpose="UART controller transmit",
        ),
        SignalSpec(
            name="RX",
            controller_names=("RX", "RXD", "UART_RX", "RXI"),
            peripheral_names=("TX", "TXD", "UART_TX", "TXO"),
            purpose="UART controller receive",
        ),
    ),
    shared_outputs_forbidden=False,
)

# No aliases and no default: both ends of a GPIO connection must be answered.
GPIO_SPEC = ProtocolSpec(
    protocol=Protocol.GPIO,
    net_prefix="NET",
    signals=(
        SignalSpec(
            name="GPIO",
            controller_names=(),
            peripheral_names=(),
            purpose="GPIO signal",
        ),
    ),
    shared_outputs_forbidden=False,
)

POWER_SPEC = ProtocolSpec(
    protocol=Protocol.POWER,
    net_prefix="PWR",
    signals=(),
    shared_outputs_forbidden=False,
)

PROTOCOL_SPECS: dict[str, ProtocolSpec] = {
    spec.protocol.value: spec
    for spec in (I2C_SPEC, SPI_SPEC, UART_SPEC, GPIO_SPEC, POWER_SPEC)
}

SUPPORTED_PROTOCOLS = frozenset(PROTOCOL_SPECS)


def spec_for(protocol: Protocol | str | None) -> ProtocolSpec | None:
    if protocol is None:
        return None
    key = protocol.value if isinstance(protocol, Protocol) else str(protocol).upper()
    return PROTOCOL_SPECS.get(key)
