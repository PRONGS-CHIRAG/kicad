"""Deterministic parsing of the free-text instruction into planning parameters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Ordering is behaviour, not style: the loop breaks on the first match, so a
# request that says "I2C ... GPIO21" must resolve to I2C, not GPIO.
PROTOCOL_PATTERNS = {
    "I2C": r"\bi\s?[²2]\s?c\b|\bi2c\b|\btwi\b",
    "SPI": r"\bspi\b",
    "UART": r"\buart\b|\bserial\b",
    # `\bgpio\b` deliberately does NOT match a pin token such as "GPIO21"
    # ('O' and '2' are both word characters, so there is no boundary between
    # them). Naming a pin is not asking for a bare GPIO connection.
    "GPIO": r"\bgpio\b",
    # POWER needs an explicit phrase. A bare "power" token appears incidentally
    # in instructions about pull-ups and rails and must not hijack them.
    "POWER": (
        r"\bpower\s+(?:up|the|this|these|it)\b"
        r"|\bconnect\s+power\b"
        r"|\bpower\s+rail\b"
        r"|\bpower[\s-]only\b"
        r"|\bjust\s+power\b"
        r"|\bpower\s+and\s+ground\b"
    ),
}

VOLTAGE_PATTERN = r"(\d(?:[.,]\d)?)\s*[vV]\b"
PULLUP_VALUE_PATTERN = r"(\d+(?:[.,]\d+)?)\s*k(?:ilo)?\s*(?:Ω|ω|ohms?)?"
REFERENCE_PATTERN = r"\b([A-Z]{1,3}\d{1,3})\b"
PIN_PATTERN = r"\b(GPIO\s?\d{1,2}|IO\s?\d{1,2}|P[A-D]\d{1,2})\b"

#: Signal name -> alternation of words an instruction may use for it. GPIO is
#: absent on purpose: a bare GPIO connection must be answered, never inferred.
SIGNAL_KEYWORDS: dict[str, str] = {
    "SDA": r"sda|data",
    "SCL": r"scl|clock|clk",
    "SCK": r"sck|sclk|clock|clk",
    "MOSI": r"mosi|sdo|dout",
    "MISO": r"miso|sdi|din",
    "CS": r"cs|chip\s*select|nss|csn",
    "TX": r"tx|txd|transmit",
    "RX": r"rx|rxd|receive",
}


@dataclass
class ParsedInstruction:
    protocol: str | None = None
    logic_voltage: str | None = None
    pullup_value: str | None = None
    sda_pin: str | None = None
    scl_pin: str | None = None
    #: Every signal the instruction assigned a controller pin to, keyed by the
    #: canonical signal name. SDA/SCL are mirrored into the two scalars above.
    signal_pins: dict[str, str] = field(default_factory=dict)
    mentioned_references: list[str] = field(default_factory=list)
    protected_objects: list[str] = field(default_factory=list)
    allow_new_components: bool = True
    raw: str = ""


def _normalize_pin(token: str) -> str:
    return token.replace(" ", "").upper()


def _pin_for_signal(text: str, signal: str) -> str | None:
    """Find the controller pin the instruction assigns to `signal`, in either word order."""
    patterns = (
        rf"\b(?:{signal})\b[^a-z0-9]{{0,12}}?" + PIN_PATTERN,
        PIN_PATTERN + rf"\s*(?:for|as)\s*(?:the\s*)?(?:{signal})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _normalize_pin(match.group(1))
    return None


def parse_instruction(text: str) -> ParsedInstruction:
    lowered = text.lower()
    parsed = ParsedInstruction(raw=text)

    for protocol, pattern in PROTOCOL_PATTERNS.items():
        if re.search(pattern, lowered):
            parsed.protocol = protocol
            break

    voltage = re.search(VOLTAGE_PATTERN, text)
    if voltage:
        parsed.logic_voltage = f"{voltage.group(1).replace(',', '.')}V"

    pullup = re.search(PULLUP_VALUE_PATTERN, lowered)
    if pullup:
        parsed.pullup_value = f"{pullup.group(1).replace(',', '.')}k"

    for name, keywords in SIGNAL_KEYWORDS.items():
        pin = _pin_for_signal(text, keywords)
        if pin:
            parsed.signal_pins[name] = pin
    parsed.sda_pin = parsed.signal_pins.get("SDA")
    parsed.scl_pin = parsed.signal_pins.get("SCL")

    parsed.mentioned_references = sorted(set(re.findall(REFERENCE_PATTERN, text)))

    for sentence in re.split(r"[.\n;]", text):
        if re.search(
            r"do not (?:modify|change|touch)|don'?t (?:modify|change|touch)|keep .* unchanged|protect",
            sentence,
            re.IGNORECASE,
        ):
            parsed.protected_objects.extend(re.findall(REFERENCE_PATTERN, sentence))
            if re.search(r"\busb\b", sentence, re.IGNORECASE):
                parsed.protected_objects.extend(["USB_D+", "USB_D-", "VBUS"])
    parsed.protected_objects = sorted(set(parsed.protected_objects))

    if re.search(r"do not add|no new components|without adding", lowered):
        parsed.allow_new_components = False

    return parsed
