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


SELF_CONTAINED = """Everything you need to answer is in this prompt. Do not clone or read the
repository, run commands, browse the web, or otherwise investigate - that costs
minutes per stage and adds nothing here. Answer directly from what is above."""


def render_rejection(task: AgentTask) -> str:
    """Say why the last attempt was rejected, so a re-run can converge.

    A stage that fails its gate is handed back to the agent that owns it. Asking
    the identical question again gets the identical answer - measured here as the
    same finding three times over - so the deterministic findings come with it.
    """
    if not task.prior_gate_findings:
        return ""
    listed = "\n".join(f"  - {finding}" for finding in task.prior_gate_findings)
    return (
        "\nYour previous attempt at this stage was REJECTED by a deterministic gate.\n"
        "Fix exactly these findings and keep everything else that was already correct:\n"
        f"{listed}\n"
    )


def _prompt(
    role: str,
    project: ProjectSpec,
    context: DesignContext,
    task: AgentTask,
    inputs: Mapping[str, object],
    instruction: str,
    read_only: bool = False,
    self_contained: bool = False,
) -> str:
    prior = json.dumps(inputs, default=str, sort_keys=True)
    restriction = (
        "You are read-only: produce findings, not fixes. Do not modify the project."
        if read_only
        else "Propose changes only; do not modify the project."
    )
    if self_contained:
        restriction = f"{restriction} {SELF_CONTAINED}"
    return f"""You are the {role} on a virtual PCB engineering team.
{instruction}
{render_rejection(task)}
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
        self_contained=True,
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
            "Convert the request into measurable electrical, interface, mechanical, and "
            "acceptance requirements.\n"
            "Every requirement carries an ID whose prefix IS its category, and there are "
            "only four: PWR-NNN (category power), IF-NNN (interface), MECH-NNN (mechanical), "
            "TEST-NNN (test). Numbering starts at 001 within each prefix. Do not invent a "
            "fifth category such as quality, thermal, or EMC - file those under the closest "
            "of the four.\n"
            "Every grouped summary must be backed by identified requirements: if you list "
            "acceptance_tests, there must be TEST-NNN requirements; if you fill in "
            "mechanical, there must be MECH-NNN requirements; power.logic_voltage and each "
            "interface voltage must match the value of a PWR/IF requirement numerically.\n"
            "Every requirement needs a unit and a numeric or boolean value where one exists. "
            "Aim for at most 15 dense requirements - every later stage reads this document, "
            "so a long list slows the whole team down."
        ),
        self_contained=True,
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
        (
            "Describe functional blocks and compatible inter-block signals without designing "
            "individual wires.\n"
            "Assign every requirement to a block: each requirement ID in the requirements "
            "document must appear in at least one block's requirement_ids, and every ID you "
            "write must exist in that document. Connections may only name blocks you declared."
        ),
        self_contained=True,
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
        self_contained=True,
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
        self_contained=True,
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
        self_contained=True,
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
