"""Prompt builders for the ten specialist sessions."""

from __future__ import annotations

import json
from collections.abc import Mapping

from .schemas import AgentTask, DesignContext, ProjectSpec


def render_design_context(context: DesignContext) -> str:
    """Render the useful design facts without burying them in a JSON document."""
    lines = [
        "Design context:",
        (f"ERC baseline: {context.erc_baseline.errors} errors, {context.erc_baseline.warnings} warnings"),
    ]
    if context.drc_baseline is not None:
        lines.append(
            f"DRC baseline: {context.drc_baseline.errors} errors, {context.drc_baseline.warnings} warnings"
        )
    if context.board is not None:
        min_x, min_y, max_x, max_y = context.board.outline
        lines.append(
            f"Board: {context.board.width_mm:g} x {context.board.height_mm:g} mm "
            f"(outline {min_x:g},{min_y:g} to {max_x:g},{max_y:g})"
        )
        if context.board.footprints:
            lines.append("Placed footprints (only these existing references can be moved):")
            lines.append("reference | x | y | extent (min_x,min_y,max_x,max_y)")
            lines.extend(
                f"{footprint.reference} | {footprint.x_mm:g} | {footprint.y_mm:g} | "
                f"{_format_extent(footprint.extent)}"
                for footprint in context.board.footprints
            )
        else:
            lines.append("Placed footprints: none")
        if context.board.drc_violations:
            lines.append("Baseline KiCad DRC violations (verbatim evidence):")
            for violation in context.board.drc_violations:
                lines.append(f"{violation.severity} | {violation.type} | {violation.description}")
                lines.extend(f"  item: {item}" for item in violation.items)
        else:
            lines.append("Baseline KiCad DRC violations: none reported; do not invent constraint values.")
        lines.append(
            "MVP layout cannot add new footprints; report symbols without footprints as limitations."
        )
    else:
        lines.append("Board: none")
    lines.append("Pin table:")
    lines.append("reference | value | lib_id | pin | name | electrical type")
    for component in context.components:
        for pin in component.pins:
            lines.append(
                f"{component.reference} | {component.value} | {component.lib_id} | "
                f"{pin.number} | {pin.name} | {pin.electrical_type}"
            )
    lines.append("Nets:")
    for net in context.nets:
        lines.append(f"{net.name}: {', '.join(net.pins)}")
    return "\n".join(lines)


def _format_extent(extent: tuple[float, float, float, float] | None) -> str:
    if extent is None:
        return "unavailable"
    return ",".join(f"{value:g}" for value in extent)


def _prompt(
    role: str,
    project: ProjectSpec,
    context: DesignContext,
    task: AgentTask,
    inputs: Mapping[str, object],
    instruction: str,
    read_only: bool = False,
) -> str:
    prior = json.dumps(inputs, default=str, sort_keys=True)
    rework_instruction = ""
    if inputs.get("rework_findings"):
        rework_instruction = (
            " This is a routed rework invocation. The findings below came from a deterministic gate "
            "or a real KiCad run and are authoritative: address each finding in the new proposal; do "
            "not argue with, negotiate, or override the gate decision."
        )
    restriction = (
        "You are read-only: produce findings, not fixes. Do not modify the project."
        if read_only
        else "Propose changes only; do not modify the project."
    )
    return f"""You are the {role} on a virtual PCB engineering team.
{instruction}{rework_instruction}

Project specification:
{project.model_dump_json(exclude={"schema_version", "design_context"})}

{render_design_context(context)}

Assigned task:
{task.model_dump_json(exclude={"schema_version"})}

Declared prior-stage outputs only:
{prior}

{restriction} Do not edit files, use shell commands to change files, or open a pull
request. Return only the structured output matching your assigned schema. Do not
include markdown, prose outside the structured output, or additional keys."""


def build_project_manager_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    return _prompt(
        "project manager",
        project,
        context,
        task,
        inputs,
        "Coordinate the workflow and identify the responsible return target. Do not emit circuit design.",
    )


def build_requirements_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    return _prompt(
        "requirements engineer",
        project,
        context,
        task,
        inputs,
        (
            "Convert the request into measurable requirements. Use exactly these canonical "
            "categories and ID prefixes: power=PWR, interface=IF, mechanical=MECH, "
            "manufacturing=MFG, cost=COST, temperature=TEMP, acceptance/test=TEST. "
            "Every ID must use the literal PREFIX-NNN format (for example PWR-001 or "
            "TEST-002). Synonyms such as supply, communication, physical, fabrication, "
            "budget, thermal, validation, and acceptance are accepted, but prefer the "
            "canonical category names."
        ),
    )


def build_architecture_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    return _prompt(
        "system architect",
        project,
        context,
        task,
        inputs,
        "Describe functional blocks and compatible inter-block signals without designing individual wires.",
    )


def build_components_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    return _prompt(
        "component engineer",
        project,
        context,
        task,
        inputs,
        (
            "Select concrete parts for the declared blocks. Do not invent numeric "
            "specifications; identify sources. The symbol and footprint fields must each "
            "be a bare Library:Name library identifier with no parentheses, commentary, "
            "or alternatives. Put explanations in reason or verified_constraints instead."
        ),
    )


def build_schematic_design_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    return _prompt(
        "schematic design engineer",
        project,
        context,
        task,
        inputs,
        (
            "Propose only supported schematic connection intents and report assumptions. "
            "Power symbols and PWR_FLAG symbols are net markers, not connectable intent "
            "targets. Connecting an already-netted ordinary pin to a new net moves its "
            "existing label when it is safely isolated; do not assume additive labels merge "
            "or split nets."
        ),
    )


def build_pcb_layout_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    return _prompt(
        "PCB layout engineer",
        project,
        context,
        task,
        inputs,
        (
            "Propose placement only within the MVP scope. Only references in the placed-footprint "
            "inventory can be moved; this MVP cannot add new footprints to the board. If a schematic "
            "symbol has no board footprint, report it as a limitation rather than placing it. The "
            "inventory extents are derived from the actual pads and graphic geometry; use them for "
            "spacing decisions. Both manufacturer-profile limits and KiCad's enforced constraints "
            "apply, and the stricter applicable constraint governs. Baseline DRC values above are "
            "actual report evidence, not assumptions; if none were reported, do not invent them. Do "
            "not claim routing that was not performed."
        ),
    )


def build_simulation_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    return _prompt(
        "simulation engineer",
        project,
        context,
        task,
        inputs,
        (
            "Perform only closed-form power, regulator, LED, divider, pull-up, and "
            "rating-margin analysis. Use exactly one status per test: pass, failed, or "
            "unverified. Free-form expected measurement values must include units; required_ma, "
            "available_ma, margin_percent, and measured_v already encode their units in the field "
            "name. Tests with a pass or failed verdict must cite either a requirement ID "
            "(PREFIX-NNN), a datasheet source, or a standards/specification citation with a "
            "section, table, clause, page, or revision locator; unverified tests must state a "
            "concise explicit reason in the reason field (older outputs may use explanatory "
            "non-numeric measured_v, required_ma, available_ma, or margin_percent text); unverified "
            "is not a silent escape hatch."
        ),
    )


def build_verification_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    return _prompt(
        "hardware verification engineer",
        project,
        context,
        task,
        inputs,
        (
            "Independently report requirement and ERC/DRC findings. Every finding needs "
            "rule, actual, expected, object, evidence, and severity. Use decision status "
            "passed, failed, or unverified, and finding severity error, warning, or info."
        ),
        read_only=True,
    )


def build_manufacturing_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    return _prompt(
        "DFM/DFA engineer",
        project,
        context,
        task,
        inputs,
        (
            "Check the named manufacturer profile and report manufacturability findings "
            "without claiming unsupported readiness. Use dfm_status passed, failed, or "
            "unverified; finding severity must be error, warning, or info."
        ),
        read_only=True,
    )


def build_qa_release_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    return _prompt(
        "QA and release engineer",
        project,
        context,
        task,
        inputs,
        (
            "Apply the final release gate and report the package manifest. Never silently "
            "fix design errors. Use release_status ready for engineering review, "
            "needs_human_review, or approved, and provide every named checklist item as "
            "a boolean."
        ),
        read_only=True,
    )
