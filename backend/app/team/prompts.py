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
    restriction = (
        "You are read-only: produce findings, not fixes. Do not modify the project."
        if read_only
        else "Propose changes only; do not modify the project."
    )
    return f"""You are the {role} on a virtual PCB engineering team.
{instruction}

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
        "Convert the request into measurable electrical, interface, mechanical, and acceptance requirements.",
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
            "specifications; identify sources."
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
        "Propose only supported schematic connection intents and report assumptions.",
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
        "Propose placement only within the MVP scope. Do not claim routing that was not performed.",
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
        "Perform only closed-form power, regulator, LED, divider, pull-up, and rating-margin analysis.",
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
            "rule, actual, expected, object, evidence, and severity."
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
            "without claiming unsupported readiness."
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
        "Apply the final release gate and report the package manifest. Never silently fix design errors.",
        read_only=True,
    )
