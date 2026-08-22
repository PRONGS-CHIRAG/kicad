#!/usr/bin/env python3
"""Generate the KiCAD fixture projects used by the demo and the benchmark suite."""

from __future__ import annotations

import argparse
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


@dataclass(frozen=True)
class PinDef:
    number: str
    name: str
    etype: str
    x: float
    y: float
    angle: float


def pin_sexpr(pin: PinDef) -> str:
    return f"""      (pin {pin.etype} line (at {pin.x} {pin.y} {pin.angle}) (length 2.54)
        (name "{pin.name}" (effects (font (size 1.27 1.27))))
        (number "{pin.number}" (effects (font (size 1.27 1.27))))
      )"""


def lib_symbol(lib_id: str, ref_prefix: str, value: str, pins: list[PinDef], half_height: float) -> str:
    body = "\n".join(pin_sexpr(p) for p in pins)
    name = lib_id.split(":")[-1]
    return f"""    (symbol "{lib_id}"
      (pin_names (offset 1.016))
      (exclude_from_sim no)
      (in_bom yes)
      (on_board yes)
      (property "Reference" "{ref_prefix}" (at 0 {half_height + 2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Value" "{value}" (at 0 {-half_height - 2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "{name}_0_1"
        (rectangle (start -10.16 {half_height}) (end 10.16 {-half_height})
          (stroke (width 0.254) (type default))
          (fill (type background))
        )
      )
      (symbol "{name}_1_1"
{body}
      )
    )"""


POWER_SYMBOL = """    (symbol "power:{name}"
      (power)
      (pin_numbers hide)
      (pin_names (offset 0) hide)
      (exclude_from_sim no)
      (in_bom yes)
      (on_board yes)
      (property "Reference" "#PWR" (at 0 -3.81 0) (effects (font (size 1.27 1.27)) hide))
      (property "Value" "{name}" (at 0 3.556 0) (effects (font (size 1.27 1.27))))
      (symbol "{name}_0_1"
        (polyline (pts (xy -0.762 1.27) (xy 0 2.54) (xy 0.762 1.27))
          (stroke (width 0) (type default))
          (fill (type none))
        )
      )
      (symbol "{name}_1_1"
        (pin {etype} line (at 0 0 90) (length 0)
          (name "{name}" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )"""

R_SYMBOL = """    (symbol "Device:R"
      (pin_numbers hide)
      (pin_names (offset 0))
      (exclude_from_sim no)
      (in_bom yes)
      (on_board yes)
      (property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
      (property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at -1.778 0 90) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "R_0_1"
        (rectangle (start -1.016 -2.54) (end 1.016 2.54)
          (stroke (width 0.254) (type default))
          (fill (type none))
        )
      )
      (symbol "R_1_1"
        (pin passive line (at 0 3.81 270) (length 1.27)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
        (pin passive line (at 0 -3.81 90) (length 1.27)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27))))
        )
      )
    )"""


ESP32_PINS = [
    PinDef("1", "3V3", "power_in", -12.7, 10.16, 0),
    PinDef("2", "GND", "power_in", -12.7, 7.62, 0),
    PinDef("3", "GPIO21", "bidirectional", -12.7, 5.08, 0),
    PinDef("4", "GPIO22", "bidirectional", -12.7, 2.54, 0),
    PinDef("5", "GPIO25", "bidirectional", -12.7, 0, 0),
    PinDef("6", "USB_D+", "bidirectional", 12.7, 10.16, 180),
    PinDef("7", "USB_D-", "bidirectional", 12.7, 7.62, 180),
    PinDef("8", "EN", "input", 12.7, 5.08, 180),
]

SENSOR_PINS = [
    PinDef("1", "VCC", "power_in", -12.7, 5.08, 0),
    PinDef("2", "GND", "power_in", -12.7, 2.54, 0),
    PinDef("3", "SDA", "bidirectional", -12.7, 0, 0),
    PinDef("4", "SCL", "input", -12.7, -2.54, 0),
    PinDef("5", "ADD0", "input", -12.7, -5.08, 0),
]

SENSOR_PINS_UNNAMED = [
    PinDef("1", "P1", "power_in", -12.7, 5.08, 0),
    PinDef("2", "P2", "power_in", -12.7, 2.54, 0),
    PinDef("3", "P3", "bidirectional", -12.7, 0, 0),
    PinDef("4", "P4", "input", -12.7, -2.54, 0),
    PinDef("5", "P5", "input", -12.7, -5.08, 0),
]

USB_PINS = [
    PinDef("1", "VBUS", "passive", -12.7, 5.08, 0),
    PinDef("2", "D-", "passive", -12.7, 2.54, 0),
    PinDef("3", "D+", "passive", -12.7, 0, 0),
    PinDef("4", "GND", "passive", -12.7, -2.54, 0),
]


def symbol_instance(lib_id: str, ref: str, value: str, x: float, y: float, project: str, sheet_uuid: str) -> str:
    return f"""  (symbol
    (lib_id "{lib_id}")
    (at {x} {y} 0)
    (unit 1)
    (exclude_from_sim no)
    (in_bom yes)
    (on_board yes)
    (dnp no)
    (uuid "{uuid.uuid4()}")
    (property "Reference" "{ref}" (at {x} {y - 14} 0) (effects (font (size 1.27 1.27))))
    (property "Value" "{value}" (at {x} {y + 14} 0) (effects (font (size 1.27 1.27))))
    (instances
      (project "{project}"
        (path "/{sheet_uuid}" (reference "{ref}") (unit 1))
      )
    )
  )"""


def global_label(name: str, x: float, y: float) -> str:
    return f"""  (global_label "{name}"
    (shape bidirectional)
    (at {x} {y} 0)
    (fields_autoplaced yes)
    (effects (font (size 1.27 1.27)) (justify left))
    (uuid "{uuid.uuid4()}")
    (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {x} {y} 0)
      (effects (font (size 1.27 1.27)) (hide yes))
    )
  )"""


def pin_position(origin: tuple[float, float], pin: PinDef) -> tuple[float, float]:
    return (origin[0] + pin.x, origin[1] - pin.y)


@dataclass
class Project:
    name: str
    sensor_pins: list[PinDef]
    connect_sensor: bool = False
    existing_pullups: bool = False
    sensor_value: str = "TMP102"
    board: bool = False


def build_schematic(project: Project) -> str:
    sheet_uuid = str(uuid.uuid4())
    mcu_at = (76.2, 63.5)
    sensor_at = (165.1, 63.5)
    usb_at = (76.2, 127.0)
    rail_3v3 = (254.0, 38.1)
    rail_gnd = (254.0, 63.5)

    lib_symbols = [
        lib_symbol("MCU:ESP32-WROOM-32", "U", "ESP32-WROOM-32", ESP32_PINS, 12.7),
        lib_symbol("Sensor:TMP102", "U", project.sensor_value, project.sensor_pins, 7.62),
        lib_symbol("Connector:USB_B_Micro", "J", "USB_B_Micro", USB_PINS, 7.62),
        R_SYMBOL,
        POWER_SYMBOL.format(name="+3V3", etype="power_in"),
        POWER_SYMBOL.format(name="GND", etype="power_in"),
        POWER_SYMBOL.format(name="PWR_FLAG", etype="power_out"),
    ]

    body: list[str] = [
        symbol_instance("MCU:ESP32-WROOM-32", "U1", "ESP32-WROOM-32", *mcu_at, project.name, sheet_uuid),
        symbol_instance("Sensor:TMP102", "U2", project.sensor_value, *sensor_at, project.name, sheet_uuid),
        symbol_instance("Connector:USB_B_Micro", "J1", "USB_B_Micro", *usb_at, project.name, sheet_uuid),
        symbol_instance("power:+3V3", "#PWR01", "+3V3", *rail_3v3, project.name, sheet_uuid),
        symbol_instance("power:PWR_FLAG", "#FLG01", "PWR_FLAG", *rail_3v3, project.name, sheet_uuid),
        symbol_instance("power:GND", "#PWR02", "GND", *rail_gnd, project.name, sheet_uuid),
        symbol_instance("power:PWR_FLAG", "#FLG02", "PWR_FLAG", *rail_gnd, project.name, sheet_uuid),
        global_label("+3V3", *rail_3v3),
        global_label("GND", *rail_gnd),
    ]

    def esp(name: str) -> tuple[float, float]:
        return pin_position(mcu_at, next(p for p in ESP32_PINS if p.name == name))

    def sensor(number: str) -> tuple[float, float]:
        return pin_position(sensor_at, next(p for p in project.sensor_pins if p.number == number))

    def usb(name: str) -> tuple[float, float]:
        return pin_position(usb_at, next(p for p in USB_PINS if p.name == name))

    body += [
        global_label("+3V3", *esp("3V3")),
        global_label("GND", *esp("GND")),
        global_label("USB_D+", *esp("USB_D+")),
        global_label("USB_D-", *esp("USB_D-")),
        global_label("+3V3", *esp("EN")),
        global_label("USB_D+", *usb("D+")),
        global_label("USB_D-", *usb("D-")),
        global_label("GND", *usb("GND")),
        global_label("VBUS", *usb("VBUS")),
        global_label("GND", *sensor("5")),
    ]

    if project.connect_sensor:
        body += [
            global_label("+3V3", *sensor("1")),
            global_label("GND", *sensor("2")),
            global_label("I2C_SDA", *sensor("3")),
            global_label("I2C_SCL", *sensor("4")),
            global_label("I2C_SDA", *esp("GPIO21")),
            global_label("I2C_SCL", *esp("GPIO22")),
        ]

    if project.existing_pullups:
        r1_at = (215.9, 38.1)
        r2_at = (228.6, 38.1)
        body += [
            symbol_instance("Device:R", "R1", "4.7k", *r1_at, project.name, sheet_uuid),
            symbol_instance("Device:R", "R2", "4.7k", *r2_at, project.name, sheet_uuid),
            global_label("+3V3", r1_at[0], r1_at[1] - 3.81),
            global_label("I2C_SDA", r1_at[0], r1_at[1] + 3.81),
            global_label("+3V3", r2_at[0], r2_at[1] - 3.81),
            global_label("I2C_SCL", r2_at[0], r2_at[1] + 3.81),
        ]

    return f"""(kicad_sch
  (version 20231120)
  (generator "eeschema")
  (generator_version "8.0")
  (uuid "{sheet_uuid}")
  (paper "A4")
  (lib_symbols
{chr(10).join(lib_symbols)}
  )
{chr(10).join(body)}
  (sheet_instances
    (path "/" (page "1"))
  )
)
"""


def pcb_pad(number: str, x: float, y: float, net: tuple[int, str] | None = None) -> str:
    net_clause = f' (net {net[0]} "{net[1]}")' if net else ""
    return (
        f'    (pad "{number}" smd roundrect (at {x} {y}) (size 2.4 1.6)'
        f' (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.2){net_clause})'
    )


def pcb_footprint(
    name: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    width: float,
    height: float,
    pads: list[str],
) -> str:
    return f"""  (footprint "Mitos:{name}" (layer "F.Cu") (at {x} {y})
    (attr smd)
    (fp_text reference "{reference}" (at 0 {-height / 2 - 2}) (layer "F.SilkS")
      (effects (font (size 1.2 1.2) (thickness 0.2))))
    (fp_text value "{value}" (at 0 {height / 2 + 2}) (layer "F.Fab")
      (effects (font (size 1 1) (thickness 0.15))))
    (fp_rect (start {-width / 2} {-height / 2}) (end {width / 2} {height / 2})
      (stroke (width 0.3) (type default)) (fill none) (layer "F.SilkS"))
{chr(10).join(pads)}
  )"""


def build_board(project: Project) -> str:
    """Build a small, self-contained KiCad 8 board for the render fixture."""
    nets = {
        "+3V3": (1, "+3V3"),
        "GND": (2, "GND"),
        "USB_D+": (3, "USB_D+"),
        "USB_D-": (4, "USB_D-"),
        "VBUS": (5, "VBUS"),
    }
    u1_pads = [
        pcb_pad("1", -6, -3.81, nets["+3V3"]),
        pcb_pad("2", -6, -1.27, nets["GND"]),
        pcb_pad("3", -6, 1.27),
        pcb_pad("4", -6, 3.81),
        pcb_pad("5", 6, 3.81),
        pcb_pad("6", 6, 1.27, nets["USB_D+"]),
        pcb_pad("7", 6, -1.27, nets["USB_D-"]),
        pcb_pad("8", 6, -3.81, nets["+3V3"]),
    ]
    u2_pads = [
        pcb_pad("1", -4, -5),
        pcb_pad("2", -4, -2.5),
        pcb_pad("3", -4, 0),
        pcb_pad("4", -4, 2.5),
        pcb_pad("5", -4, 5, nets["GND"]),
    ]
    j1_pads = [
        pcb_pad("1", -5, -3.81, nets["VBUS"]),
        pcb_pad("2", -5, -1.27, nets["USB_D-"]),
        pcb_pad("3", -5, 1.27, nets["USB_D+"]),
        pcb_pad("4", -5, 3.81, nets["GND"]),
    ]
    return f"""(kicad_pcb (version 20240108) (generator "pcbnew")
  (general (thickness 1.6))
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (36 "B.SilkS" user "b.silkscreen")
    (37 "F.SilkS" user "f.silkscreen")
    (44 "Edge.Cuts" user)
  )
  (setup
    (pad_to_mask_clearance 0)
    (allow_soldermask_bridges_in_footprints no)
  )
  (net 0 "")
  (net 1 "+3V3")
  (net 2 "GND")
  (net 3 "USB_D+")
  (net 4 "USB_D-")
  (net 5 "VBUS")
{pcb_footprint("ESP32-WROOM-32", "U1", "ESP32-WROOM-32", 65, 65, 18, 13, u1_pads)}
{pcb_footprint("TMP102", "U2", project.sensor_value, 95, 55, 12, 14, u2_pads)}
{pcb_footprint("USB_B_Micro", "J1", "USB_B_Micro", 120, 75, 14, 12, j1_pads)}
  (segment (start 71 66.27) (end 105 66.27) (width 0.5) (layer "F.Cu") (net 3))
  (segment (start 105 66.27) (end 105 76.27) (width 0.5) (layer "F.Cu") (net 3))
  (segment (start 105 76.27) (end 115 76.27) (width 0.5) (layer "F.Cu") (net 3))
  (segment (start 71 63.73) (end 101 63.73) (width 0.5) (layer "F.Cu") (net 4))
  (segment (start 101 63.73) (end 101 73.73) (width 0.5) (layer "F.Cu") (net 4))
  (segment (start 101 73.73) (end 115 73.73) (width 0.5) (layer "F.Cu") (net 4))
  (segment (start 59 61.19) (end 53 55) (width 0.5) (layer "F.Cu") (net 1))
  (segment (start 53 55) (end 71 55) (width 0.5) (layer "F.Cu") (net 1))
  (segment (start 71 55) (end 71 61.19) (width 0.5) (layer "F.Cu") (net 1))
  (segment (start 59 63.73) (end 53 63.73) (width 0.5) (layer "F.Cu") (net 2))
  (segment (start 53 63.73) (end 53 60) (width 0.5) (layer "F.Cu") (net 2))
  (segment (start 53 60) (end 91 60) (width 0.5) (layer "F.Cu") (net 2))
  (segment (start 115 78.81) (end 110 83) (width 0.5) (layer "F.Cu") (net 2))
  (segment (start 110 83) (end 53 83) (width 0.5) (layer "F.Cu") (net 2))
  (gr_line (start 35 35) (end 145 35) (stroke (width 0.4) (type default)) (layer "Edge.Cuts"))
  (gr_line (start 145 35) (end 145 100) (stroke (width 0.4) (type default)) (layer "Edge.Cuts"))
  (gr_line (start 145 100) (end 35 100) (stroke (width 0.4) (type default)) (layer "Edge.Cuts"))
  (gr_line (start 35 100) (end 35 35) (stroke (width 0.4) (type default)) (layer "Edge.Cuts"))
  (gr_text "ESP32 I2C DEMO" (at 90 40) (layer "F.SilkS")
    (effects (font (size 2 2) (thickness 0.35))))
  (gr_text "USB" (at 120 87) (layer "F.SilkS")
    (effects (font (size 1.2 1.2) (thickness 0.2))))
)
"""


PRO_TEMPLATE = """{
  "board": {},
  "boards": [],
  "cvpcb": {"equivalence_files": []},
  "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
  "meta": {"filename": "%(name)s.kicad_pro", "version": 1},
  "net_settings": {
    "classes": [
      {"bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
       "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
       "name": "Default", "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
       "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6}
    ],
    "meta": {"version": 3},
    "net_colors": null
  },
  "pcbnew": {"page_layout_descr_file": ""},
  "schematic": {
    "legacy_lib_dir": "",
    "legacy_lib_list": [],
    "meta": {"version": 1},
    "page_layout_descr_file": ""
  },
  "sheets": [],
  "text_variables": {}
}
"""


PROJECTS = [
    Project(name="esp32_i2c_demo", sensor_pins=SENSOR_PINS),
    Project(name="esp32_i2c_existing_pullups", sensor_pins=SENSOR_PINS, existing_pullups=True),
    Project(name="esp32_i2c_unnamed_pins", sensor_pins=SENSOR_PINS_UNNAMED),
    Project(name="esp32_i2c_connected", sensor_pins=SENSOR_PINS, connect_sensor=True, existing_pullups=True),
    Project(name="esp32_i2c_board", sensor_pins=SENSOR_PINS, board=True),
]


# --------------------------------------------------------------------------------
# Multi-protocol fixtures (SPI / UART / GPIO / power-only).
#
# Strictly additive: the I2C projects above keep their own symbols and their own
# build_schematic() path so their bytes never move. New protocols get a richer MCU
# plus one peripheral symbol each, generated by build_protocol_schematic().
# --------------------------------------------------------------------------------

# Pin NUMBERS deliberately mirror the GPIO number, so Component.pin("18") and
# Component.pin("GPIO18") resolve to the same pin. The alias-named pins (SCK/MOSI/
# MISO/CS/TX/RX) sit on numbers outside the GPIO set so they can never collide.
# Electrical types are from the controller's point of view: it drives clock, MOSI
# and chip select, and listens on MISO/RX.
ESP32_DEVKIT_PINS = [
    PinDef("1", "3V3", "power_in", -12.7, 10.16, 0),
    PinDef("2", "GND", "power_in", -12.7, 7.62, 0),
    PinDef("5", "GPIO5", "bidirectional", -12.7, 5.08, 0),
    PinDef("18", "GPIO18", "bidirectional", -12.7, 2.54, 0),
    PinDef("19", "GPIO19", "bidirectional", -12.7, 0, 0),
    PinDef("21", "GPIO21", "bidirectional", -12.7, -2.54, 0),
    PinDef("22", "GPIO22", "bidirectional", -12.7, -5.08, 0),
    PinDef("23", "GPIO23", "bidirectional", -12.7, -7.62, 0),
    PinDef("25", "GPIO25", "bidirectional", 12.7, 10.16, 180),
    PinDef("30", "SCK", "output", 12.7, 7.62, 180),
    PinDef("31", "MOSI", "output", 12.7, 5.08, 180),
    PinDef("32", "MISO", "input", 12.7, 2.54, 180),
    PinDef("33", "CS", "output", 12.7, 0, 180),
    PinDef("34", "TX", "output", 12.7, -2.54, 180),
    PinDef("35", "RX", "input", 12.7, -5.08, 180),
    PinDef("36", "EN", "input", 12.7, -7.62, 180),
]

# SPI display: the controller drives SCK/MOSI/CS, the display answers on MISO.
# MISO being a real `output` is what makes the "reject only when BOTH endpoints
# are outputs" rule distinguishable from the I2C "reject any output" rule.
SPI_DISPLAY_PINS = [
    PinDef("1", "VCC", "power_in", -12.7, 5.08, 0),
    PinDef("2", "GND", "power_in", -12.7, 2.54, 0),
    PinDef("3", "SCK", "input", -12.7, 0, 0),
    PinDef("4", "MOSI", "input", -12.7, -2.54, 0),
    PinDef("5", "MISO", "output", -12.7, -5.08, 0),
    PinDef("6", "CS", "input", -12.7, -7.62, 0),
]

# UART module: TX is an output, RX an input, so the crossover TX->RX / RX->TX is
# electrically valid while TX->TX would be two outputs on one net.
UART_MODULE_PINS = [
    PinDef("1", "VCC", "power_in", -12.7, 5.08, 0),
    PinDef("2", "GND", "power_in", -12.7, 2.54, 0),
    PinDef("3", "TX", "output", -12.7, 0, 0),
    PinDef("4", "RX", "input", -12.7, -2.54, 0),
]

# GPIO peripheral: no protocol-aliasable signal names at all, so the planner has
# to ask for both pins. FAULT is an output, giving a both-outputs pair against the
# controller's TX pin.
GPIO_MODULE_PINS = [
    PinDef("1", "VCC", "power_in", -12.7, 5.08, 0),
    PinDef("2", "GND", "power_in", -12.7, 2.54, 0),
    PinDef("3", "IN1", "input", -12.7, 0, 0),
    PinDef("4", "IN2", "input", -12.7, -2.54, 0),
    PinDef("5", "FAULT", "output", -12.7, -5.08, 0),
]

# Power-only peripheral: nothing but a supply and a return.
POWER_LOAD_PINS = [
    PinDef("1", "VCC", "power_in", -12.7, 2.54, 0),
    PinDef("2", "GND", "power_in", -12.7, 0, 0),
]

MCU_LIB_ID = "MCU:ESP32-DEVKITC-32"
MCU_VALUE = "ESP32-DEVKITC-32"


@dataclass(frozen=True)
class Peripheral:
    """The single non-power peripheral placed as U2 in a protocol fixture."""

    lib_id: str
    value: str
    pins: list[PinDef]
    half_height: float


SPI_DISPLAY = Peripheral("Display:ILI9341", "ILI9341", SPI_DISPLAY_PINS, 10.16)
UART_MODULE = Peripheral("RF:HC-05", "HC-05", UART_MODULE_PINS, 7.62)
GPIO_MODULE = Peripheral("Relay:RELAY_2CH", "RELAY-2CH", GPIO_MODULE_PINS, 7.62)
POWER_LOAD = Peripheral("Device:Fan", "FAN_5015", POWER_LOAD_PINS, 5.08)


@dataclass
class ProtocolProject:
    """A fixture with the richer MCU plus one protocol peripheral, both unwired."""

    name: str
    peripheral: Peripheral


def build_protocol_schematic(project: ProtocolProject) -> str:
    sheet_uuid = str(uuid.uuid4())
    mcu_at = (76.2, 63.5)
    peripheral_at = (165.1, 63.5)
    rail_3v3 = (254.0, 38.1)
    rail_gnd = (254.0, 63.5)
    peripheral = project.peripheral

    lib_symbols = [
        lib_symbol(MCU_LIB_ID, "U", MCU_VALUE, ESP32_DEVKIT_PINS, 12.7),
        lib_symbol(peripheral.lib_id, "U", peripheral.value, peripheral.pins, peripheral.half_height),
        R_SYMBOL,
        POWER_SYMBOL.format(name="+3V3", etype="power_in"),
        POWER_SYMBOL.format(name="GND", etype="power_in"),
        POWER_SYMBOL.format(name="PWR_FLAG", etype="power_out"),
    ]

    def mcu(name: str) -> tuple[float, float]:
        return pin_position(mcu_at, next(p for p in ESP32_DEVKIT_PINS if p.name == name))

    body: list[str] = [
        symbol_instance(MCU_LIB_ID, "U1", MCU_VALUE, *mcu_at, project.name, sheet_uuid),
        symbol_instance(peripheral.lib_id, "U2", peripheral.value, *peripheral_at, project.name, sheet_uuid),
        symbol_instance("power:+3V3", "#PWR01", "+3V3", *rail_3v3, project.name, sheet_uuid),
        symbol_instance("power:PWR_FLAG", "#FLG01", "PWR_FLAG", *rail_3v3, project.name, sheet_uuid),
        symbol_instance("power:GND", "#PWR02", "GND", *rail_gnd, project.name, sheet_uuid),
        symbol_instance("power:PWR_FLAG", "#FLG02", "PWR_FLAG", *rail_gnd, project.name, sheet_uuid),
        global_label("+3V3", *rail_3v3),
        global_label("GND", *rail_gnd),
        # The MCU's own rails. These labels are also what put +3V3 and GND into
        # ProjectState.nets: the reader skips power-symbol pins, so a rail only
        # becomes resolvable once a real component pin sits on it.
        global_label("+3V3", *mcu("3V3")),
        global_label("GND", *mcu("GND")),
        global_label("+3V3", *mcu("EN")),
    ]

    return f"""(kicad_sch
  (version 20231120)
  (generator "eeschema")
  (generator_version "8.0")
  (uuid "{sheet_uuid}")
  (paper "A4")
  (lib_symbols
{chr(10).join(lib_symbols)}
  )
{chr(10).join(body)}
  (sheet_instances
    (path "/" (page "1"))
  )
)
"""


PROTOCOL_PROJECTS = [
    ProtocolProject(name="esp32_spi_display", peripheral=SPI_DISPLAY),
    ProtocolProject(name="esp32_uart_module", peripheral=UART_MODULE),
    ProtocolProject(name="esp32_gpio_peripheral", peripheral=GPIO_MODULE),
    ProtocolProject(name="esp32_power_only", peripheral=POWER_LOAD),
]

ALL_PROJECTS: list = [*PROJECTS, *PROTOCOL_PROJECTS]


def write_project(project: Project | ProtocolProject, out_dir: Path) -> Path:
    target = out_dir / project.name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    if isinstance(project, ProtocolProject):
        (target / f"{project.name}.kicad_sch").write_text(build_protocol_schematic(project))
        (target / f"{project.name}.kicad_pro").write_text(PRO_TEMPLATE % {"name": project.name})
        return target
    (target / f"{project.name}.kicad_sch").write_text(build_schematic(project))
    (target / f"{project.name}.kicad_pro").write_text(PRO_TEMPLATE % {"name": project.name})
    if project.board:
        (target / f"{project.name}.kicad_pcb").write_text(build_board(project))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=FIXTURES / "projects")
    parser.add_argument("--project", choices=[project.name for project in ALL_PROJECTS])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    projects = [project for project in ALL_PROJECTS if args.project is None or project.name == args.project]
    for project in projects:
        path = write_project(project, args.out)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
