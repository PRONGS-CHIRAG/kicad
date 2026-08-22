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
]


def write_project(project: Project, out_dir: Path) -> Path:
    target = out_dir / project.name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    (target / f"{project.name}.kicad_sch").write_text(build_schematic(project))
    (target / f"{project.name}.kicad_pro").write_text(PRO_TEMPLATE % {"name": project.name})
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=FIXTURES / "projects")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for project in PROJECTS:
        path = write_project(project, args.out)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
