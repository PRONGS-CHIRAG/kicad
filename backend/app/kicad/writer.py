"""Structured, minimal mutations of a KiCAD schematic document."""

from __future__ import annotations

import uuid as uuidlib
from pathlib import Path

from . import sexpr
from .sexpr import Symbol

S = Symbol

R_LIB_SYMBOL = """
(symbol "Device:R"
  (pin_numbers hide)
  (pin_names (offset 0))
  (exclude_from_sim no)
  (in_bom yes)
  (on_board yes)
  (property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
  (property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
  (property "Footprint" "" (at -1.778 0 90) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (property "Description" "Resistor" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
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
)
"""


def new_uuid() -> str:
    return str(uuidlib.uuid4())


def load(path: Path) -> list:
    return sexpr.loads(Path(path).read_text())


def save(doc: list, path: Path) -> None:
    Path(path).write_text(sexpr.dumps(doc) + "\n")


def root_uuid(doc: list) -> str:
    node = sexpr.find(doc, "uuid")
    return sexpr.atom(node) if node is not None else ""


def _effects(justify: str | None = None) -> list:
    effects: list = [S("effects"), [S("font"), [S("size"), S("1.27"), S("1.27")]]]
    if justify:
        effects.append([S("justify"), S(justify)])
    return effects


def has_global_label(doc: list, name: str, x: float, y: float) -> bool:
    for label in sexpr.find_all(doc, "global_label"):
        at = sexpr.find(label, "at")
        if at is None:
            continue
        if (
            sexpr.atom(label) == name
            and abs(sexpr.number(at, 1) - x) < 0.01
            and abs(sexpr.number(at, 2) - y) < 0.01
        ):
            return True
    return False


def add_global_label(doc: list, name: str, x: float, y: float, angle: float = 0) -> bool:
    """Attach a named global label at a sheet coordinate (idempotent)."""
    if has_global_label(doc, name, x, y):
        return False
    label = [
        S("global_label"),
        name,
        [S("shape"), S("bidirectional")],
        [S("at"), S(_fmt(x)), S(_fmt(y)), S(_fmt(angle))],
        [S("fields_autoplaced"), S("yes")],
        _effects("left"),
        [S("uuid"), new_uuid()],
        [
            S("property"),
            "Intersheetrefs",
            "${INTERSHEET_REFS}",
            [S("at"), S(_fmt(x)), S(_fmt(y)), S(_fmt(angle))],
            [S("effects"), [S("font"), [S("size"), S("1.27"), S("1.27")]], [S("hide"), S("yes")]],
        ],
    ]
    doc.append(label)
    return True


def _fmt(value: float) -> str:
    return f"{value:g}"


def ensure_lib_symbol(doc: list, lib_id: str, definition: str) -> None:
    lib_node = sexpr.find(doc, "lib_symbols")
    if lib_node is None:
        lib_node = [S("lib_symbols")]
        doc.insert(1, lib_node)
    for symbol in sexpr.find_all(lib_node, "symbol"):
        if sexpr.atom(symbol) == lib_id:
            return
    lib_node.append(sexpr.loads(definition))


def add_symbol_instance(
    doc: list,
    lib_id: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    project_name: str,
    angle: float = 0,
) -> str:
    """Place a symbol instance on the sheet and return its uuid."""
    instance_uuid = new_uuid()
    symbol = [
        S("symbol"),
        [S("lib_id"), lib_id],
        [S("at"), S(_fmt(x)), S(_fmt(y)), S(_fmt(angle))],
        [S("unit"), S("1")],
        [S("exclude_from_sim"), S("no")],
        [S("in_bom"), S("yes")],
        [S("on_board"), S("yes")],
        [S("dnp"), S("no")],
        [S("uuid"), instance_uuid],
        [
            S("property"),
            "Reference",
            reference,
            [S("at"), S(_fmt(x + 2.54)), S(_fmt(y - 1.27)), S("0")],
            _effects("left"),
        ],
        [
            S("property"),
            "Value",
            value,
            [S("at"), S(_fmt(x + 2.54)), S(_fmt(y + 1.27)), S("0")],
            _effects("left"),
        ],
        [
            S("instances"),
            [
                S("project"),
                project_name,
                [
                    S("path"),
                    f"/{root_uuid(doc)}",
                    [S("reference"), reference],
                    [S("unit"), S("1")],
                ],
            ],
        ],
    ]
    doc.append(symbol)
    return instance_uuid


def next_reference(doc: list, prefix: str) -> str:
    used = set()
    for symbol in sexpr.find_all(doc, "symbol"):
        for prop in sexpr.find_all(symbol, "property"):
            if sexpr.atom(prop, 1) == "Reference":
                used.add(sexpr.atom(prop, 2))
    index = 1
    while f"{prefix}{index}" in used:
        index += 1
    return f"{prefix}{index}"


def free_area(doc: list) -> tuple[float, float]:
    """A sheet coordinate below every existing symbol, used for added parts."""
    max_y = 0.0
    min_x = 1e9
    for symbol in sexpr.find_all(doc, "symbol"):
        at = sexpr.find(symbol, "at")
        if at is None:
            continue
        max_y = max(max_y, sexpr.number(at, 2))
        min_x = min(min_x, sexpr.number(at, 1))
    if min_x > 1e8:
        min_x = 50.0
    return min_x, max_y + 25.4
