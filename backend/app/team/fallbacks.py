"""Derived outputs for offline team runs.

These functions only copy facts from the request, the loaded design context, or
declared tool inputs. Missing facts remain unresolved.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

from ..planning.instruction import parse_instruction
from . import checks
from .profiles import get_profile
from .schemas import (
    AgentTask,
    Architecture,
    ComponentSelection,
    DesignContext,
    LayoutProposal,
    ManufacturingFinding,
    ManufacturingReport,
    MechanicalRequirements,
    PMPlan,
    PowerRequirements,
    ProjectSpec,
    ReleaseRecord,
    Requirement,
    RequirementsDoc,
    SchematicIntents,
    SelectedComponent,
    SimulationMarginTest,
    SimulationRailTest,
    SimulationReport,
    SimulationTest,
    VerificationReport,
)


def _context(project: ProjectSpec) -> DesignContext | None:
    return project.design_context


def requirements_fallback(
    project: ProjectSpec, task: AgentTask, inputs: Mapping[str, object]
) -> RequirementsDoc:
    parsed = parse_instruction(project.request)
    requirements: list[Requirement] = []
    if parsed.logic_voltage is not None:
        requirements.append(
            Requirement(
                id="PWR-001",
                category="power",
                statement="Logic voltage stated in the project request",
                value=parsed.logic_voltage.removesuffix("V"),
                unit="V",
                source="project request",
            )
        )
    if parsed.protocol is not None:
        requirements.append(
            Requirement(
                id="IF-001",
                category="interface",
                statement="Interface protocol stated in the project request",
                value=parsed.protocol,
                unit="enum",
                source="project request",
            )
        )
    if parsed.pullup_value is not None:
        requirements.append(
            Requirement(
                id="IF-002",
                category="interface",
                statement="Pull-up value stated in the project request",
                value=parsed.pullup_value.removesuffix("k"),
                unit="kΩ",
                source="project request",
            )
        )
    return RequirementsDoc(
        requirements=requirements,
        power=PowerRequirements(
            input="",
            logic_voltage=parsed.logic_voltage or "",
            maximum_current_ma=None,
        ),
        interfaces=(
            [
                {
                    "type": parsed.protocol,
                    "voltage": parsed.logic_voltage or "",
                    "devices": None,
                }
            ]
            if parsed.protocol
            else []
        ),
        mechanical=MechanicalRequirements(
            maximum_width_mm=None,
            maximum_height_mm=None,
            layers=None,
        ),
        constraints=list(project.constraints),
        acceptance_tests=[],
    )


def architecture_fallback(
    project: ProjectSpec, task: AgentTask, inputs: Mapping[str, object]
) -> Architecture:
    return Architecture(
        blocks=[],
        connections=[],
        unresolved_questions=[
            "Functional architecture cannot be derived from schematic component identities alone."
        ],
    )


def components_fallback(
    project: ProjectSpec, task: AgentTask, inputs: Mapping[str, object]
) -> ComponentSelection:
    context = _context(project)
    if context is None:
        return ComponentSelection(components=[])
    return ComponentSelection(
        components=[
            SelectedComponent(
                reference_group=component.reference,
                manufacturer_part="",
                quantity=1,
                symbol=component.lib_id,
                footprint="",
                reason="copied from schematic design context; procurement identity unverified",
                verified_constraints=[],
                specifications=[],
            )
            for component in context.components
        ]
    )


def simulation_fallback(
    project: ProjectSpec, task: AgentTask, inputs: Mapping[str, object]
) -> SimulationReport:
    tests: list[SimulationTest] = []
    requirements = inputs.get("requirements")
    if isinstance(requirements, RequirementsDoc) and requirements.power.logic_voltage:
        tests.append(
            SimulationRailTest(
                name="declared logic rail",
                expected={"nominal": requirements.power.logic_voltage},
                measured_v="unverified",
                status="unverified; no circuit model",
                source=_logic_voltage_source(requirements),
            )
        )
        voltage = float(requirements.power.logic_voltage.removesuffix("V"))
        context = _context(project)
        if context is not None:
            for component in context.components:
                match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(k)?(?:Ω|ohm|r)?\s*", component.value, re.I)
                if (
                    component.reference.upper().startswith("R")
                    and ("resistor" in component.lib_id.lower() or ":R" in component.lib_id)
                    and match
                ):
                    resistance = float(match.group(1)) * (1000 if match.group(2) else 1)
                    current_ma = voltage / resistance * 1000
                    tests.append(
                        SimulationMarginTest(
                            name=f"{component.reference} resistor current, formula V/R",
                            required_ma=f"{current_ma:g} mA",
                            available_ma="unverified",
                            margin_percent="unverified",
                            status=(
                                "unverified; available rail current is not specified; "
                                "assumes R across the logic rail; not verified from netlist"
                            ),
                            source=_logic_voltage_source(requirements),
                        )
                    )
    return SimulationReport(
        tests=tests,
        models=[],
        assumptions=["No circuit model available; reported values remain unverified"],
    )


def _logic_voltage_source(requirements: RequirementsDoc) -> str:
    for requirement in requirements.requirements:
        if requirement.category.lower() == "power" and requirement.unit == "V":
            return f"requirement {requirement.id}"
    return "unverified requirement source"


def manufacturing_fallback(
    project: ProjectSpec, task: AgentTask, inputs: Mapping[str, object]
) -> ManufacturingReport:
    profile_name = str(project.manufacturer_profile.get("name", ""))
    try:
        profile = get_profile(profile_name)
    except ValueError:
        return ManufacturingReport(
            manufacturer_profile=profile_name,
            dfm_status="unverified",
            findings=[
                ManufacturingFinding(
                    type="profile",
                    net="",
                    severity="error",
                    recommendation="Define a known manufacturer profile",
                )
            ],
            fabrication_ready=False,
            profile_rules=None,
        )
    drc = project.design_context.drc_baseline if project.design_context else None
    findings = []
    if drc is None or drc.errors or drc.warnings:
        findings.append(
            ManufacturingFinding(
                type="drc",
                net="",
                severity="error" if drc and drc.errors else "warning",
                recommendation="Resolve or verify DRC findings against the profile",
            )
        )
    return ManufacturingReport(
        manufacturer_profile=profile.name,
        dfm_status="unverified" if findings else "passed",
        findings=findings,
        fabrication_ready=not findings,
        profile_provenance=profile.provenance,
        profile_rules={
            "minimum_trace_width_mm": profile.minimum_trace_width_mm,
            "minimum_spacing_mm": profile.minimum_spacing_mm,
            "minimum_drill_mm": profile.minimum_drill_mm,
            "copper_to_edge_clearance_mm": profile.copper_to_edge_clearance_mm,
            "supported_layer_count": float(profile.supported_layer_count),
        },
    )


def qa_release_fallback(project: ProjectSpec, task: AgentTask, inputs: Mapping[str, object]) -> ReleaseRecord:
    # The task carries the release manifest the gate will check against, so the
    # fallback hashes the same files in the same order rather than re-deriving
    # the set from whatever a caller happened to pass in.
    manifest = task.release_manifest or {}
    candidates = manifest.get("included_files") or inputs.get("project_files", [])
    paths = [Path(item) for item in candidates if isinstance(item, str)]
    files = [path for path in paths if path.is_file()]
    release_hash = checks.release_hash(files)
    checklist_input = inputs.get("release_checklist")
    checklist = (
        {key: value for key, value in checklist_input.items() if isinstance(value, bool)}
        if isinstance(checklist_input, Mapping)
        # Offline there is no judgement to attest with, so every item is false
        # and the release is held. Reporting no checklist at all instead used to
        # fail the gate's completeness rule, which left every offline run ending
        # on a finding about the shape of the record rather than about the board.
        else {key: False for key in checks.RELEASE_CHECKLIST_ITEMS}
    )
    return ReleaseRecord(
        release_status="unverified" if not files else "needs_human_review",
        project_version=project.project_id,
        included_files=[str(path) for path in files],
        open_critical_findings=0,
        release_hash=release_hash,
        checklist=checklist,
    )


def fallback_for(
    agent_id: str, project: ProjectSpec, task: AgentTask, inputs: Mapping[str, object]
) -> BaseModel:
    if agent_id == "project_manager":
        return PMPlan(project_goal=project.request, workflow=[], status="unverified")
    if agent_id == "requirements":
        return requirements_fallback(project, task, inputs)
    if agent_id == "architecture":
        return architecture_fallback(project, task, inputs)
    if agent_id == "components":
        return components_fallback(project, task, inputs)
    if agent_id == "schematic_design":
        return SchematicIntents(
            protocol="",
            logic_voltage="",
            pullup_value="",
            protected_objects=list(project.protected_objects),
            assumptions=["No schematic intent is established by deterministic fallback"],
            intents=[],
        )
    if agent_id == "pcb_layout":
        return LayoutProposal(placements=[], critical_nets=[], unrouted_nets=[])
    if agent_id == "simulation":
        return simulation_fallback(project, task, inputs)
    if agent_id == "verification":
        return VerificationReport(
            requirements_total=len(project.requirements),
            requirements_passed=0,
            requirements_failed=0,
            requirements_unverified=len(project.requirements),
            critical_findings=[],
            decision="unverified",
        )
    if agent_id == "manufacturing":
        return manufacturing_fallback(project, task, inputs)
    if agent_id == "qa_release":
        return qa_release_fallback(project, task, inputs)
    raise ValueError(f"unknown team agent {agent_id!r}")
