"""Deterministic stage gates for team outputs and KiCAD artifacts."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from ..decision import unexpected_file_changes
from ..kicad import sexpr
from ..kicad.board import board_outline, footprint_extent, footprint_reference, load
from ..kicad.erc import diff_violations
from ..kicad.reader import ProjectState
from ..kicad.state import diff_states
from ..models import ErcReport
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
        if source.output_voltage and target.input_voltage and source.output_voltage != target.input_voltage:
            findings.append(
                _finding(
                    "connected block voltage compatibility",
                    f"{source.output_voltage}->{target.input_voltage}",
                    "matching voltages",
                    connection.signal,
                    "architecture",
                    "error",
                )
            )
    for block in architecture.blocks:
        required = set(block.required_inputs or []) | set(block.required_outputs or [])
        assigned = set(block.requirement_ids or [])
        for item in required - assigned:
            findings.append(
                _finding(
                    "required inputs and outputs assigned",
                    item,
                    "assigned",
                    block.id,
                    "architecture",
                    "error",
                )
            )
        if (
            block.power_required_ma is not None
            and block.power_available_ma is not None
            and block.power_required_ma > block.power_available_ma
        ):
            findings.append(
                _finding(
                    "power budget",
                    block.power_required_ma,
                    block.power_available_ma,
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
            missing = sorted(key for key in required if not values.get(key))
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
                    "no new ERC violations", violation.description, "none", "ERC", "kicad-cli ERC", "error"
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
        if (
            extent[0] < min_x + manufacturer.copper_to_edge_clearance_mm
            or extent[1] < min_y + manufacturer.copper_to_edge_clearance_mm
            or extent[2] > max_x - manufacturer.copper_to_edge_clearance_mm
            or extent[3] > max_y - manufacturer.copper_to_edge_clearance_mm
        ):
            findings.append(
                _finding(
                    "edge clearance",
                    reference,
                    manufacturer.copper_to_edge_clearance_mm,
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
    connected_names = {
        str(net[2])
        for pad in sexpr.find_all(document, "pad")
        for net in sexpr.find_all(pad, "net")
        if len(net) >= 3
    }
    for net in required_nets:
        if net not in names or net not in connected_names:
            findings.append(
                _finding("required net connected", net, "present on board", net, str(board_path), "error")
            )
    if drc_after is not None and not drc_after.ran:
        findings.append(_finding("DRC has run", False, True, str(board_path), "kicad-cli DRC", "error"))
    if drc_before is not None and drc_after is not None:
        for violation in diff_violations(drc_before, drc_after).new_critical:
            findings.append(
                _finding(
                    "no new DRC violations", violation.description, "none", "DRC", "kicad-cli DRC", "error"
                )
            )
    return _check("pcb_layout", findings, project_version, "board/DRC checker", skipped_count)


def check_simulation(
    report: SimulationReport,
    requirements: RequirementsDoc | None = None,
    *,
    project_version: str,
) -> StageCheckResult:
    findings: list[CheckFinding] = []
    requirement_ids = (
        {requirement.id for requirement in requirements.requirements} if requirements is not None else set()
    )
    for test in report.tests:
        numeric_values: list[object]
        if isinstance(test, SimulationRailTest):
            numeric_values = [*test.expected.values(), test.measured_v]
        else:
            numeric_values = [test.required_ma, test.available_ma, test.margin_percent]
        unitless = [
            value
            for value in numeric_values
            if isinstance(value, (int, float))
            or (isinstance(value, str) and bool(re.fullmatch(r"\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)\s*", value)))
        ]
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
        if "unverified" in test.status.lower():
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
        requirement_match = re.search(r"\b((?:PWR|IF|MECH|TEST)-\d{3})\b", source)
        traceable = (
            bool(requirement_match and requirement_match.group(1) in requirement_ids)
            or "datasheet" in source.lower()
        )
        if not traceable:
            findings.append(
                _finding(
                    "simulation range traceability",
                    source,
                    "datasheet or requirement source",
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
                    missing,
                    [],
                    item.requirement_id,
                    "verification report",
                    "error",
                )
            )
        if not item.evidence:
            findings.append(
                _finding(
                    "verification evidence source",
                    {},
                    "non-empty evidence",
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
            _finding(
                "profile values used",
                report.dfm_status,
                "passed",
                "manufacturing",
                "manufacturing report",
                "error",
            )
        )
    return _check("manufacturing", findings, project_version, "manufacturer checker")


def check_qa_release(
    release: ReleaseRecord,
    files: Sequence[Path | str],
    project_version: str,
) -> StageCheckResult:
    findings: list[CheckFinding] = []
    paths = [Path(path) for path in files]
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()]
    expected_hash = hashlib.sha256("".join(hashes).encode()).hexdigest() if hashes else ""
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
    checklist = release.checklist
    if not checklist or set(checklist) != set(RELEASE_CHECKLIST_ITEMS) or not all(checklist.values()):
        findings.append(
            _finding(
                "release checklist",
                checklist or {},
                "all checklist items true",
                "release",
                "release checklist",
                "error",
            )
        )
    if release.release_status == "approved" and findings:
        findings.append(
            _finding(
                "release checklist", "approved", "needs human review", "release", "release checklist", "error"
            )
        )
    return _check("qa_release", findings, project_version, "release checker")
