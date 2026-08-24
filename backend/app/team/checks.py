"""Deterministic stage gates for team outputs and KiCAD artifacts."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from ..decision import unexpected_file_changes
from ..kicad import sexpr
from ..kicad.board import (
    board_outline,
    copper_edge_clearance,
    footprint_extent,
    footprint_reference,
    load,
)
from ..kicad.erc import diff_violations
from ..kicad.reader import ProjectState
from ..kicad.state import diff_states
from ..models import ErcReport, Violation
from .profiles import ManufacturerProfile, get_profile
from .schemas import (
    Architecture,
    CheckFinding,
    ComponentSelection,
    DesignContext,
    EvidenceRecord,
    ManufacturingReport,
    ReleaseRecord,
    Requirement,
    RequirementsDoc,
    SimulationRailTest,
    SimulationReport,
    StageCheckResult,
    VerificationReport,
)

RELEASE_CHECKLIST_ITEMS = (
    "all_agents_completed",
    "no_open_critical_findings",
    "erc_passes",
    "drc_passes",
    "simulation_tests_pass",
    "requirements_traceable",
    "bom_complete",
    "symbols_match_footprints",
    "fabrication_files_generated",
    "files_match_project_version",
    "no_unexpected_release_changes",
)


def _check(
    stage: str,
    findings: list[CheckFinding],
    project_version: str,
    source: str,
    skipped_count: int = 0,
) -> StageCheckResult:
    passed = not any(item.severity == "error" for item in findings)
    return StageCheckResult(
        stage=stage,
        passed=passed,
        findings=findings,
        skipped_count=skipped_count,
        evidence=[
            EvidenceRecord(
                check=f"team {stage} gate",
                tool=source,
                project_version=project_version,
                timestamp=datetime.now(timezone.utc).isoformat(),
                result="passed" if passed else "failed",
                findings=[item.model_dump(mode="json") for item in findings],
            )
        ],
    )


def finding(
    rule: str, actual: object, expected: object, object_name: str, source: str, severity: str
) -> CheckFinding:
    return CheckFinding(
        rule=rule,
        actual=actual,
        expected=expected,
        kicad_object=object_name,
        evidence_source=source,
        severity=severity,
    )


_finding = finding


_QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9])([-+]?\d+(?:\.\d+)?)\s*"
    r"(µf|μf|uf|ma|mv|v|kohm|kω|k|khz|hz|pf|nf|mm|ohm|ω)?",
    re.IGNORECASE,
)
_PROTOCOL_RE = re.compile(r"(?<![A-Za-z0-9])(USB-C|I2C|UART)(?=[^A-Za-z0-9]|$)", re.IGNORECASE)
_UNIT_ALIASES = {
    "μf": "uf",
    "µf": "uf",
    "uf": "uf",
    "ma": "ma",
    "mv": "mv",
    "v": "v",
    "kohm": "kohm",
    "kω": "kohm",
    "k": "kohm",
    "khz": "khz",
    "hz": "hz",
    "pf": "pf",
    "nf": "nf",
    "mm": "mm",
    "ohm": "ohm",
    "ω": "ohm",
}


def _normalise_unit(unit: str | None) -> str | None:
    return _UNIT_ALIASES.get(unit.replace(" ", "").lower()) if unit else None


def _quantities(value: object, unit_hint: str | None = None) -> list[tuple[float, str | None]]:
    text = str(value)
    return [
        (float(match.group(1)), _normalise_unit(match.group(2)) or _normalise_unit(unit_hint))
        for match in _QUANTITY_RE.finditer(text)
    ]


_GROUND_RE = re.compile(r"\b(gnd|ground|vss|return)\b", re.IGNORECASE)
_RAIL_RE = re.compile(r"(vbus|vcc|vdd|vin|rail|supply|power|\+?\d+v\d?\b)", re.IGNORECASE)


def _volts(value: str | None) -> float | None:
    """The volts a declared block voltage names, or None if it is only prose."""
    for amount, unit in _quantities(value, "V") if value else []:
        if unit == "v":
            return amount
    return None


def _voltage_severity(signal: str, source: float, target: float) -> str | None:
    """How hard to complain that a connection joins two different voltages.

    `ArchitectureConnection` is `{from, to, signal}` - it carries no direction
    semantics, so this check cannot tell "5 V feeding a 3.3 V input" (a part
    destroyed) from "the +3V3 net also touches the regulator" (fine). Two
    restrictions keep it honest rather than noisy:

      - A signal that is not a power rail does not carry a block's supply
        voltage at all. A USB data pair between a VBUS-fed connector and a 3.3 V
        MCU is not a voltage conflict, and a ground return is not one either.
      - Feeding a higher voltage into a lower-voltage input is the case that
        breaks hardware, so that stays an error. The other direction is usually
        a shared rail modelled as a connection, so it is a warning a human can
        read rather than a gate that blocks the run.
    """
    if _GROUND_RE.search(signal) or source == 0 or target == 0:
        return None
    if not _RAIL_RE.search(signal):
        return None
    if abs(source - target) <= max(1e-9, 1e-3 * max(abs(source), abs(target), 1.0)):
        return None
    return "error" if source > target else "warning"


def _violation_text(violation: Violation) -> str:
    """A violation with the nets and items it names, not just its wording.

    KiCAD's own description says what rule broke - "Pins of type Power output
    and Power output are connected" - but not where, and a stage handed only
    that has to guess which of its intents caused it.
    """
    located = violation.nets or violation.items
    return f"{violation.description} ({', '.join(located)})" if located else violation.description


def _quantity_matches(summary: tuple[float, str | None], requirement: Requirement) -> bool:
    amount, unit = summary
    requirement_quantity = _quantities(requirement.value, requirement.unit)
    if not requirement_quantity or unit is None:
        return False
    expected, expected_unit = requirement_quantity[0]
    return unit == expected_unit and abs(amount - expected) <= max(
        1e-9, 1e-3 * max(abs(amount), abs(expected), 1.0)
    )


def check_requirements(document: RequirementsDoc, project_version: str) -> StageCheckResult:
    findings: list[CheckFinding] = []
    seen: set[str] = set()
    # No ID-versus-category check lives here: `Requirement` takes the category
    # from the ID prefix, so the two cannot disagree by the time a document
    # exists. What is still worth checking is that the grouped summaries below
    # are backed by identified requirements - see the acceptance_tests rule.
    by_category: dict[str, list[Requirement]] = {}
    for requirement in document.requirements:
        by_category.setdefault(requirement.category.lower(), []).append(requirement)
        if requirement.id in seen:
            findings.append(
                _finding(
                    "unique requirement IDs",
                    requirement.id,
                    "unique",
                    requirement.id,
                    "requirements",
                    "error",
                )
            )
        seen.add(requirement.id)
        if requirement.value is not None and not requirement.unit:
            findings.append(
                _finding(
                    "units normalized",
                    requirement.unit,
                    "unit present",
                    requirement.id,
                    "requirements",
                    "error",
                )
            )
        if isinstance(requirement.value, (int, float)) and requirement.value < 0:
            findings.append(
                _finding(
                    "range validation",
                    requirement.value,
                    "non-negative",
                    requirement.id,
                    "requirements",
                    "error",
                )
            )
    statements: dict[tuple[str, str], object] = {}
    for requirement in document.requirements:
        key = (requirement.category.lower(), requirement.statement.lower())
        if key in statements and statements[key] != requirement.value:
            findings.append(
                _finding(
                    "conflicting requirements",
                    requirement.value,
                    statements[key],
                    requirement.id,
                    "requirements",
                    "error",
                )
            )
        statements[key] = requirement.value
    power_requirements = by_category.get("power", [])
    if document.power.logic_voltage and power_requirements:
        summary_quantities = _quantities(document.power.logic_voltage)
        if not summary_quantities:
            findings.append(
                _finding(
                    "grouped summary is machine-checkable",
                    document.power.logic_voltage,
                    "numeric quantity with unit",
                    "power.logic_voltage",
                    "requirements",
                    "warning",
                )
            )
        elif not any(
            _quantity_matches(summary_quantities[0], requirement) for requirement in power_requirements
        ):
            findings.append(
                _finding(
                    "grouped and identified requirements agree",
                    document.power.logic_voltage,
                    [requirement.value for requirement in power_requirements],
                    "power.logic_voltage",
                    "requirements",
                    "error",
                )
            )
    interface_requirements = by_category.get("interface", [])
    interface_text = " ".join(
        f"{requirement.statement} {requirement.value}".lower() for requirement in interface_requirements
    )
    for interface in document.interfaces:
        protocol = _PROTOCOL_RE.search(interface.type or "")
        voltage_quantities = _quantities(interface.voltage)
        if not protocol and not voltage_quantities:
            findings.append(
                _finding(
                    "grouped summary is machine-checkable",
                    interface.type or interface.voltage,
                    "numeric quantity or supported protocol token",
                    "interfaces",
                    "requirements",
                    "warning",
                )
            )
        elif protocol and protocol.group(1).lower() not in interface_text:
            findings.append(
                _finding(
                    "grouped and identified requirements agree",
                    protocol.group(1),
                    "interface requirement statement",
                    "interfaces",
                    "requirements",
                    "error",
                )
            )
        if voltage_quantities and not any(
            _quantity_matches(quantity, requirement)
            for quantity in voltage_quantities
            for requirement in (*interface_requirements, *power_requirements)
        ):
            findings.append(
                _finding(
                    "grouped and identified requirements agree",
                    interface.voltage,
                    "interface or power requirement quantity",
                    "interfaces",
                    "requirements",
                    "error",
                )
            )
    mechanical_values = (
        document.mechanical.maximum_width_mm,
        document.mechanical.maximum_height_mm,
        document.mechanical.layers,
    )
    if any(value is not None for value in mechanical_values) and not by_category.get("mechanical"):
        findings.append(
            _finding(
                "grouped and identified requirements agree",
                mechanical_values,
                "mechanical requirement IDs",
                "mechanical",
                "requirements",
                "error",
            )
        )
    if document.acceptance_tests and not by_category.get("test"):
        findings.append(
            _finding(
                "grouped and identified requirements agree",
                document.acceptance_tests,
                "TEST requirement IDs",
                "acceptance_tests",
                "requirements",
                "error",
            )
        )
    return _check("requirements", findings, project_version, "requirements parser")


def check_architecture(
    document: RequirementsDoc,
    architecture: Architecture,
    project_version: str,
) -> StageCheckResult:
    findings: list[CheckFinding] = []
    block_ids = {block.id for block in architecture.blocks}
    mapped = {
        requirement_id for block in architecture.blocks for requirement_id in block.requirement_ids or []
    }
    for requirement in document.requirements:
        if requirement.id not in mapped:
            findings.append(
                _finding(
                    "requirements map to blocks",
                    requirement.id,
                    "mapped requirement ID",
                    "architecture",
                    "architecture",
                    "error",
                )
            )
    for connection in architecture.connections:
        if connection.from_block not in block_ids or connection.to_block not in block_ids:
            findings.append(
                _finding(
                    "connected blocks exist",
                    connection.model_dump(mode="json"),
                    "known block IDs",
                    "architecture",
                    "architecture",
                    "error",
                )
            )
            continue
        source = next(block for block in architecture.blocks if block.id == connection.from_block)
        target = next(block for block in architecture.blocks if block.id == connection.to_block)
        if not source.output_voltage or not target.input_voltage:
            continue
        if source.output_voltage == target.input_voltage:
            continue
        source_volts, target_volts = _volts(source.output_voltage), _volts(target.input_voltage)
        if source_volts is None or target_volts is None:
            # Prose on either side: the same rail written two ways cannot be
            # told from a real mismatch, so it is reported, not gated on.
            findings.append(
                _finding(
                    "block voltages are machine-checkable",
                    f"{source.output_voltage}->{target.input_voltage}",
                    "a voltage with a unit on both sides",
                    connection.signal,
                    "architecture",
                    "warning",
                )
            )
            continue
        severity = _voltage_severity(connection.signal, source_volts, target_volts)
        if severity is not None:
            findings.append(
                _finding(
                    "connected block voltage compatibility",
                    f"{source.output_voltage}->{target.input_voltage}",
                    "matching voltages",
                    connection.signal,
                    "architecture",
                    severity,
                )
            )
    # A block's required_inputs/required_outputs name signals; requirement_ids
    # name requirements. Subtracting one from the other flagged every port of
    # every block on every possible output - it was unsatisfiable rather than
    # strict. What is real, and in the right namespace, is that a block which
    # declares ports is wired to something: requirement coverage is already
    # checked by "requirements map to blocks" above.
    driven = {connection.to_block for connection in architecture.connections}
    driving = {connection.from_block for connection in architecture.connections}
    available_from_suppliers: dict[str, float] = {}
    by_id = {block.id: block for block in architecture.blocks}
    for connection in architecture.connections:
        supplier = by_id.get(connection.from_block)
        if supplier is not None and supplier.power_available_ma is not None:
            available_from_suppliers[connection.to_block] = max(
                available_from_suppliers.get(connection.to_block, 0.0),
                supplier.power_available_ma,
            )
    for block in architecture.blocks:
        # A block wired in neither direction is floating, and that is what this
        # rule is for. Requiring both directions made a boundary block fail on
        # principle: the input of a USB connector comes from the host, and there
        # is no on-board block to name as its source. A block wired one way is
        # part of the graph, so the missing side is reported and not gated on.
        declares_ports = bool(block.required_inputs or block.required_outputs)
        if declares_ports and block.id not in driven and block.id not in driving:
            findings.append(
                _finding(
                    "declared ports are connected",
                    [*(block.required_inputs or []), *(block.required_outputs or [])],
                    "at least one connection",
                    block.id,
                    "architecture",
                    "error",
                )
            )
        elif declares_ports:
            if block.required_inputs and block.id not in driven:
                findings.append(
                    _finding(
                        "declared inputs are connected",
                        block.required_inputs,
                        "an incoming connection, unless the source is off-board",
                        block.id,
                        "architecture",
                        "warning",
                    )
                )
            if block.required_outputs and block.id not in driving:
                findings.append(
                    _finding(
                        "declared outputs are connected",
                        block.required_outputs,
                        "an outgoing connection, unless the destination is off-board",
                        block.id,
                        "architecture",
                        "warning",
                    )
                )
        # `power_available_ma` is what a block passes on, not what reaches it. A
        # leaf consumer honestly reports 0 - it supplies nothing downstream - and
        # comparing its own draw against that made every consumer in the design
        # over budget. What the budget is actually about is whether the blocks
        # feeding this one can supply what it draws.
        supplied = available_from_suppliers.get(block.id)
        if (
            block.power_required_ma is not None
            and supplied is not None
            and block.power_required_ma > supplied
        ):
            findings.append(
                _finding(
                    "power budget",
                    f"{block.power_required_ma:g} mA drawn",
                    f"{supplied:g} mA offered by the blocks feeding it",
                    block.id,
                    "architecture",
                    "error",
                )
            )
    return _check("architecture", findings, project_version, "architecture checker")


def check_components(
    selection: ComponentSelection,
    context: DesignContext | None = None,
    *,
    project_version: str,
) -> StageCheckResult:
    findings: list[CheckFinding] = []
    library_name = re.compile(r"^[^:\s]+:[^:\s]+$")
    known = {component.reference: component for component in context.components} if context else {}
    for component in selection.components:
        if not library_name.fullmatch(component.symbol):
            findings.append(
                _finding(
                    "symbol identifier is well formed",
                    component.symbol,
                    "Library:Name",
                    component.reference_group,
                    "component selection",
                    "error",
                )
            )
        if not library_name.fullmatch(component.footprint):
            findings.append(
                _finding(
                    "footprint identifier is well formed",
                    component.footprint,
                    "Library:Name",
                    component.reference_group,
                    "component selection",
                    "error",
                )
            )
        for specification in component.specifications or []:
            values = specification.model_dump(mode="json")
            required = {"source", "page_or_section", "minimum", "typical", "maximum"}
            # `not values.get(key)` counted a real 0 as a missing field, and a
            # datasheet minimum of zero is ordinary. Only absence is absence.
            # (`reference_group` may also name a group like "R1-R4", in which
            # case the existing-symbol lookup below simply finds nothing.)
            missing = sorted(key for key in required if values.get(key) in (None, ""))
            if missing:
                findings.append(
                    _finding(
                        "numeric specification provenance",
                        missing,
                        [],
                        component.reference_group,
                        "component selection",
                        "error",
                    )
                )
        if component.reference_group in known and component.symbol != known[component.reference_group].lib_id:
            findings.append(
                _finding(
                    "existing schematic symbol matches",
                    component.symbol,
                    known[component.reference_group].lib_id,
                    component.reference_group,
                    "schematic",
                    "error",
                )
            )
    return _check("components", findings, project_version, "component checker")


def check_schematic(
    before: ProjectState,
    after: ProjectState,
    erc: ErcReport,
    expected_pin_nets: Mapping[str, str] | None = None,
    expected_symbols: Iterable[str] = (),
    files_changed: list[str] | None = None,
    erc_baseline: ErcReport | None = None,
    *,
    project_version: str,
) -> StageCheckResult:
    findings: list[CheckFinding] = []
    state_diff = diff_states(before, after)
    expected = expected_pin_nets or {}
    for reference in expected_symbols:
        if reference not in after.components:
            findings.append(
                _finding(
                    "expected symbols exist",
                    reference,
                    "present",
                    reference,
                    "schematic state",
                    "error",
                )
            )
    for pin, net in expected.items():
        if after.pin_nets.get(pin) != net:
            findings.append(
                _finding(
                    "pin-to-net assignment", after.pin_nets.get(pin), net, pin, "schematic state", "error"
                )
            )
    if not erc.ran:
        findings.append(
            _finding("ERC has run", False, True, str(after.schematic_path), "kicad-cli ERC", "error")
        )
    if files_changed:
        unexpected = unexpected_file_changes(files_changed, touches_pcb=False)
        if unexpected:
            findings.append(
                _finding("unexpected changes", unexpected, [], "project files", "checkpoint diff", "error")
            )
    if erc_baseline is not None and erc.ran:
        new_errors = diff_violations(erc_baseline, erc).new_critical
        for violation in new_errors:
            findings.append(
                _finding(
                    "no new ERC violations",
                    _violation_text(violation),
                    "none",
                    "ERC",
                    "kicad-cli ERC",
                    "error",
                )
            )
    if state_diff.removed_components:
        findings.append(
            _finding(
                "expected symbols exist",
                state_diff.removed_components,
                [],
                "schematic",
                "schematic state diff",
                "error",
            )
        )
    return _check("schematic_design", findings, project_version, "decision/state diff")


def _clearance_advice(
    extent: tuple[float, float, float, float],
    outline: tuple[float, float, float, float],
    required: float,
) -> tuple[float, str] | None:
    """The smallest clearance to the outline, and the move that would fix it.

    Reporting a negative clearance was not enough to act on: a real run was told
    "J1 clears the outline by -2 mm", moved J1 the wrong way, was told -5 mm, and
    moved it further still until the return trips ran out. Which edge is crossed
    and which way the centre has to go are the parts a placement can use.
    """
    min_x, min_y, max_x, max_y = outline
    # Each side: how far the footprint is from the required clearance, and the
    # change in the placement's own coordinate that would close the gap.
    sides = (
        ("left", required - (extent[0] - min_x), "increase x"),
        ("right", required - (max_x - extent[2]), "decrease x"),
        ("top", required - (extent[1] - min_y), "increase y"),
        ("bottom", required - (max_y - extent[3]), "decrease y"),
    )
    short = [item for item in sides if item[1] > 0]
    if not short:
        return None
    clearance = min(
        extent[0] - min_x, extent[1] - min_y, max_x - extent[2], max_y - extent[3]
    )
    if len({side for side, _, _ in short} & {"left", "right"}) == 2:
        return clearance, "the footprint is wider than the outline allows at any x"
    if len({side for side, _, _ in short} & {"top", "bottom"}) == 2:
        return clearance, "the footprint is taller than the outline allows at any y"
    moves = "; ".join(
        f"{move} by at least {shortfall:.3g} mm to clear the {side} edge"
        for side, shortfall, move in short
    )
    return clearance, moves


def _boxes_overlap(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> bool:
    return first[0] < second[2] and first[2] > second[0] and first[1] < second[3] and first[3] > second[1]


def check_layout(
    board_path: Path | str,
    profile: ManufacturerProfile | str,
    required_nets: Iterable[str] = (),
    drc_before: ErcReport | None = None,
    drc_after: ErcReport | None = None,
    *,
    project_version: str,
) -> StageCheckResult:
    manufacturer = get_profile(profile) if isinstance(profile, str) else profile
    document = load(Path(board_path))
    min_x, min_y, max_x, max_y = board_outline(document)
    # The stricter of what the fab can make and what this board's DRC enforces.
    # Naming only the profile's figure told the stage to aim at 0.3 mm on a board
    # whose own rules reject anything under 0.5, so a placement could satisfy the
    # number it was given and still fail the DRC that runs immediately after.
    board_clearance = copper_edge_clearance(document)
    required_clearance = max(manufacturer.copper_to_edge_clearance_mm, board_clearance)
    findings: list[CheckFinding] = []
    boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    skipped_count = 0
    for footprint in sexpr.find_all(document, "footprint"):
        at = sexpr.find(footprint, "at")
        reference = footprint_reference(footprint) or "unknown"
        if at is None or len(at) < 3:
            skipped_count += 1
            findings.append(
                _finding(
                    "footprint extent available",
                    reference,
                    "measurable footprint extent",
                    reference,
                    str(board_path),
                    "warning",
                )
            )
            continue
        extent = footprint_extent(footprint)
        if extent is None:
            skipped_count += 1
            findings.append(
                _finding(
                    "footprint extent available",
                    reference,
                    "measurable footprint extent",
                    reference,
                    str(board_path),
                    "warning",
                )
            )
            continue
        boxes.append((reference, extent))
        # The measured gap, not just the name of the footprint that has one too
        # small. "J1 is J1, expected 0.3" tells a layout stage nothing about
        # which way to move or how far, so a re-run guesses; the number and the
        # outline it is measured against are what make the finding fixable.
        advice = _clearance_advice(extent, (min_x, min_y, max_x, max_y), required_clearance)
        if advice is not None:
            clearance, remedy = advice
            findings.append(
                _finding(
                    "edge clearance",
                    f"{reference} clears the outline by {clearance:.3g} mm "
                    f"(extent {extent[0]:.3g},{extent[1]:.3g} to {extent[2]:.3g},{extent[3]:.3g}; "
                    f"outline {min_x:.3g},{min_y:.3g} to {max_x:.3g},{max_y:.3g})",
                    f"at least {required_clearance:g} mm - the greater of the "
                    f"{manufacturer.name} profile's {manufacturer.copper_to_edge_clearance_mm:g} mm "
                    f"and this board's own DRC rule of {board_clearance:g} mm. {remedy}",
                    reference,
                    str(board_path),
                    "error",
                )
            )
    for index, (reference, box) in enumerate(boxes):
        for other, other_box in boxes[index + 1 :]:
            if _boxes_overlap(box, other_box):
                findings.append(
                    _finding(
                        "footprint overlap",
                        f"{reference},{other}",
                        "none",
                        reference,
                        str(board_path),
                        "error",
                    )
                )
    names = {str(net[2]) for net in sexpr.find_all(document, "net") if len(net) >= 3}
    # A pad is a child of a footprint, not of the board, and `find_all` only
    # looks at direct children. Asking the document for its pads therefore found
    # none of them, so every net the layout stage called critical was reported
    # as absent - on boards whose pads carry that very net.
    connected_names = {
        str(net[2])
        for footprint in sexpr.find_all(document, "footprint")
        for pad in sexpr.find_all(footprint, "pad")
        for net in sexpr.find_all(pad, "net")
        if len(net) >= 3
    }
    for net in required_nets:
        if net not in names:
            findings.append(
                _finding("required net connected", net, "declared on board", net, str(board_path), "error")
            )
        elif net not in connected_names:
            findings.append(
                _finding(
                    "required net connected",
                    f"{net} is declared but reaches no pad",
                    "at least one pad on the net",
                    net,
                    str(board_path),
                    "error",
                )
            )
    if drc_after is not None and not drc_after.ran:
        findings.append(_finding("DRC has run", False, True, str(board_path), "kicad-cli DRC", "error"))
    if drc_before is not None and drc_after is not None:
        for violation in diff_violations(drc_before, drc_after).new_critical:
            findings.append(
                _finding(
                    "no new DRC violations",
                    _violation_text(violation),
                    "none",
                    "DRC",
                    "kicad-cli DRC",
                    "error",
                )
            )
    return _check("pcb_layout", findings, project_version, "board/DRC checker", skipped_count)


def _is_bare_number(value: object) -> bool:
    return isinstance(value, (int, float)) or (
        isinstance(value, str) and bool(re.fullmatch(r"\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)\s*", value))
    )


_PART_TOKEN_RE = re.compile(r"[A-Za-z0-9]{4,}")


def _part_tokens(text: str) -> set[str]:
    """Tokens distinctive enough to identify a part: letters and digits together.

    "NCP1117", "USB4105", "ESP32", "RC0603FR" identify something. "Yageo",
    "series" and "datasheet" identify a vendor or a document class and would
    match almost any prose, so a token has to mix letters and digits to count.
    """
    return {
        token.casefold()
        for token in _PART_TOKEN_RE.findall(text)
        if any(character.isalpha() for character in token) and any(character.isdigit() for character in token)
    }


def _cites_part(source: str, part_tokens: set[str]) -> bool:
    """Whether the source names one of the selected parts.

    An engineer writes "RC0603" for a part ordered as "RC0603FR-075K1L" and
    "NCP1117" for "NCP1117ST33T3G", so a cited token counts when it is a prefix
    of the ordering code. Five characters is the shortest prefix that still
    picks out one part rather than a package family.
    """
    for cited in _part_tokens(source):
        for known in part_tokens:
            if cited == known or (len(cited) >= 5 and known.startswith(cited)):
                return True
    return False


def _unverified(status: str) -> bool:
    """Whether a test reports that it could not be verified.

    Real runs write this as "NOT_VERIFIED" as often as "unverified", and only
    the second spelling used to be recognised.
    """
    folded = re.sub(r"[^a-z]", "", status.casefold())
    return "unverified" in folded or "notverified" in folded


def _traceable(source: str, requirement_ids: set[str], part_tokens: set[str]) -> bool:
    """Whether a simulation test says where its numbers came from.

    Three provenances are legitimate in this pipeline, and the gate used to
    accept only the first two:

      - the word "datasheet";
      - a requirement this run actually produced, matched by its own ID rather
        than by a hard-coded prefix list - requirement IDs are the requirements
        stage's to name, and a run that used a prefix outside `PWR|IF|MECH|TEST`
        had every one of its tests rejected;
      - a part the component stage selected. That is where a simulation
        engineer's numbers come from here, and naming one was being treated as
        naming nothing.
    """
    if "datasheet" in source.casefold():
        return True
    if any(identifier and identifier in source for identifier in requirement_ids):
        return True
    return _cites_part(source, part_tokens)


def check_simulation(
    report: SimulationReport,
    requirements: RequirementsDoc | None = None,
    components: ComponentSelection | None = None,
    *,
    project_version: str,
) -> StageCheckResult:
    findings: list[CheckFinding] = []
    requirement_ids = (
        {requirement.id for requirement in requirements.requirements} if requirements is not None else set()
    )
    part_tokens: set[str] = set()
    for component in components.components if components is not None else []:
        part_tokens |= _part_tokens(component.manufacturer_part)
        for specification in component.specifications or []:
            part_tokens |= _part_tokens(specification.source)
    for test in report.tests:
        # Only `expected` is free-form. Every other numeric field on a simulation
        # test names its unit in the field name - `required_ma`, `available_ma`,
        # `margin_percent`, `measured_v` - so a bare number there is already
        # unambiguous, and demanding "600 mA" in a field called `required_ma`
        # rejected every margin test a real run produced. What is left of the
        # rule is the case it was written for: a key in `expected` that names no
        # unit paired with a bare number, where nothing says what 50 means.
        unitless = (
            [
                f"{key}={value!r}"
                for key, value in test.expected.items()
                if _is_bare_number(value) and _normalise_unit(key.rsplit("_", 1)[-1]) is None
            ]
            if isinstance(test, SimulationRailTest)
            else []
        )
        if unitless:
            findings.append(
                _finding(
                    "simulation values carry units",
                    unitless,
                    "unit-bearing values",
                    test.name,
                    "simulation report",
                    "error",
                )
            )
        if _unverified(test.status):
            findings.append(
                _finding(
                    "simulation model available",
                    test.status,
                    "verified model",
                    test.name,
                    "simulation report",
                    "warning",
                )
            )
        source = test.source or ""
        if not _traceable(source, requirement_ids, part_tokens):
            findings.append(
                _finding(
                    "simulation range traceability",
                    source,
                    "a datasheet, a requirement ID, or a selected part",
                    test.name,
                    "simulation report",
                    "error",
                )
            )
    if not report.models and not report.assumptions:
        findings.append(
            _finding(
                "models and assumptions reported",
                [],
                "models or assumptions",
                "simulation",
                "simulation report",
                "error",
            )
        )
    return _check("simulation", findings, project_version, "simulation checker")


def check_verification(report: VerificationReport, project_version: str) -> StageCheckResult:
    findings: list[CheckFinding] = []
    for item in report.critical_findings:
        missing = [
            key
            for key in (
                "rule",
                "actual",
                "expected",
                "kicad_object",
                "evidence_source",
                "severity",
            )
            if item.model_dump().get(key) in (None, "")
        ]
        if missing:
            findings.append(
                _finding(
                    "verification finding completeness",
                    f"empty or absent: {', '.join(missing)}",
                    "every field filled",
                    item.requirement_id,
                    "verification report",
                    "error",
                )
            )
        if not item.evidence:
            findings.append(
                # Named "verification evidence source" before, while the field at
                # fault is `evidence` and a separate `evidence_source` sits right
                # beside it - which the stage had filled in correctly. It was
                # being told the wrong field was wrong, so its repair returned
                # the same document and failed the same way.
                _finding(
                    "verification finding has evidence",
                    "evidence is {}",
                    "an object recording what the source showed, not an empty one",
                    item.requirement_id,
                    "verification report",
                    "error",
                )
            )
    return _check("verification", findings, project_version, "verification checker")


def check_manufacturing(
    report: ManufacturingReport, profile_name: str, project_version: str
) -> StageCheckResult:
    findings: list[CheckFinding] = []
    try:
        profile = get_profile(profile_name)
    except ValueError:
        findings.append(
            _finding(
                "manufacturer profile defined",
                profile_name,
                "known profile",
                "manufacturing",
                "profiles.py",
                "error",
            )
        )
        return _check("manufacturing", findings, project_version, "manufacturer checker")
    if report.manufacturer_profile != profile.name:
        findings.append(
            _finding(
                "profile named",
                report.manufacturer_profile,
                profile.name,
                "manufacturing",
                "manufacturing report",
                "error",
            )
        )
    expected_rules = {
        "minimum_trace_width_mm": profile.minimum_trace_width_mm,
        "minimum_spacing_mm": profile.minimum_spacing_mm,
        "minimum_drill_mm": profile.minimum_drill_mm,
        "copper_to_edge_clearance_mm": profile.copper_to_edge_clearance_mm,
        "supported_layer_count": float(profile.supported_layer_count),
    }
    if report.profile_rules != expected_rules:
        findings.append(
            _finding(
                "profile values used",
                report.profile_rules or {},
                expected_rules,
                "manufacturing",
                "profiles.py",
                "error",
            )
        )
    if report.profile_provenance != profile.provenance:
        findings.append(
            _finding(
                "profile provenance exposed",
                report.profile_provenance or "",
                profile.provenance,
                "manufacturing",
                "profiles.py",
                "error",
            )
        )
    if report.fabrication_ready and report.dfm_status != "passed":
        findings.append(
            # Was labelled "profile values used" too, so a report that had the
            # profile right and its readiness wrong was told the profile was
            # wrong. The rule a finding names is how a stage knows what to fix.
            _finding(
                "fabrication readiness agrees with DFM status",
                f"fabrication_ready with dfm_status {report.dfm_status!r}",
                "dfm_status 'passed', or fabrication_ready false",
                "manufacturing",
                "manufacturing report",
                "error",
            )
        )
    return _check("manufacturing", findings, project_version, "manufacturer checker")


RELEASE_FILE_PATTERNS = ("*.kicad_sch", "*.kicad_pcb", "*.kicad_pro")


def release_files(project_dir: Path | str) -> list[Path]:
    """The project files a release is made of, in a fixed order.

    The order is part of the definition: the release hash is taken over these
    files in sequence, so a directory listing that came back in a different
    order would produce a different hash for an unchanged project.
    """
    directory = Path(project_dir)
    return sorted(path for pattern in RELEASE_FILE_PATTERNS for path in directory.glob(pattern))


def release_hash(files: Sequence[Path | str]) -> str:
    """The hash of a release: one digest over the digests of its files.

    The QA stage is asked to report this and the gate recomputes it, so both
    sides have to derive it the same way from the same definition.
    """
    hashes = [
        hashlib.sha256(path.read_bytes()).hexdigest() for item in files if (path := Path(item)).is_file()
    ]
    return hashlib.sha256("".join(hashes).encode()).hexdigest() if hashes else ""


def check_qa_release(
    release: ReleaseRecord,
    files: Sequence[Path | str],
    project_version: str,
) -> StageCheckResult:
    findings: list[CheckFinding] = []
    expected_hash = release_hash(files)
    actual_hash = release.release_hash
    if not actual_hash or actual_hash != expected_hash:
        findings.append(
            _finding(
                "release uses real file hashes",
                actual_hash,
                expected_hash,
                "release",
                "release files",
                "error",
            )
        )
    checklist = release.checklist or {}
    missing = sorted(set(RELEASE_CHECKLIST_ITEMS) - set(checklist))
    unknown = sorted(set(checklist) - set(RELEASE_CHECKLIST_ITEMS))
    if missing or unknown:
        findings.append(
            _finding(
                "release checklist is complete",
                {"missing": missing, "unexpected": unknown},
                sorted(RELEASE_CHECKLIST_ITEMS),
                "release",
                "release checklist",
                "error",
            )
        )
    # Every item true used to be required outright, which made "drc_passes:
    # false" an error on a run whose DRC really had failed - the only way past
    # the gate was to attest to something untrue. What the checklist is for is
    # deciding the release, so that is what is checked: all true is the
    # condition for approving, and a false item is a reason to hold, not a
    # malformed document.
    unmet = sorted(item for item, met in checklist.items() if not met)
    if release.release_status == "approved" and (unmet or findings):
        findings.append(
            _finding(
                "approval requires a clean checklist",
                unmet or "a failing release gate",
                "needs_human_review",
                "release",
                "release checklist",
                "error",
            )
        )
    return _check("qa_release", findings, project_version, "release checker")
