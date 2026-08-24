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
            "write must exist in that document. Connections may only name blocks you "
            "declared, and a block that declares required_inputs or required_outputs must "
            "appear in at least one connection.\n"
            "Give input_voltage and output_voltage as a number with a unit (\"3.3 V\", not "
            "\"regulated logic rail\"), so they can be compared. Never drive a higher "
            "voltage into a lower-voltage input."
        ),
        self_contained=True,
    )


def _render_symbol_constraint(context: DesignContext) -> str:
    """Say out loud that this pipeline cannot change a schematic symbol.

    It cannot: the schematic stage has three intents and all three wire pins
    that already exist. The gate has always enforced that, but nothing told the
    component engineer, so a run that needed a different connector was rejected
    once and then "fixed" by keeping the old symbol next to the new part's
    footprint - a document that passes the gate and describes a board nobody
    could build. Saying the constraint up front is what makes the gate
    answerable on the first attempt.
    """
    if not context.components:
        return ""
    listed = "\n".join(
        f"  - {component.reference} is {component.lib_id} with {len(component.pins)} pins"
        for component in context.components
        if not component.reference.startswith("#")
    )
    return (
        "This pipeline can wire pins that already exist; it cannot add a symbol to the "
        "schematic or change one. The schematic already has:\n"
        f"{listed}\n"
        "Select a part for each of those reference designators. Symbol must stay exactly "
        "the lib_id shown, and the footprint you name must belong to that same symbol - do "
        "not pair a retained symbol with a different part's footprint in order to fit a "
        "part the schematic cannot hold. A part the design needs but the schematic has no "
        "symbol for may still be listed, as long as its reference_group says plainly that "
        "it is proposed and not in the schematic, so the stages after you do not try to "
        "wire a pin that does not exist. Reporting that limit is this stage doing its job; "
        "papering over it is not.\n"
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
            "specifications; identify sources.\n"
            f"{_render_symbol_constraint(context)}"
            "Give symbol and footprint as Library:Name. Every numeric specification you "
            "list needs a source and a page or section; leave the list out rather than "
            "filling those in from memory."
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
            "Propose only supported schematic connection intents and report assumptions.\n"
            "Every pin you name must appear in the pin table above, written exactly as "
            "reference.number - there is no intent that adds a symbol, so a pin that is not "
            "in that table cannot be created and the intent naming it will be rejected. If "
            "the design needs a part that is not in the table, leave it out of the intents "
            "and record it in assumptions."
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
            "Independently report requirement and ERC/DRC findings.\n"
            "A finding carries two separate things and both are required. `evidence_source` "
            "names the tool or document it came from, as a string. `evidence` is an object "
            "recording what that source actually showed - for instance "
            '{"tool": "kicad-cli ERC", "net": "VBUS", "observed": "two power outputs"} - and '
            "it may not be left empty; an empty object is a finding with nothing behind it. "
            "Fill `rule`, `actual`, `expected`, `kicad_object` and `severity` for every "
            "finding too, and give requirement_outcomes one entry per requirement ID in the "
            "requirements document so the totals can be traced."
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
            "without claiming unsupported readiness.\n"
            "The project specification above carries the whole profile. Copy its "
            "provenance verbatim into profile_provenance, and report profile_rules as "
            "exactly these five keys taken from it: minimum_trace_width_mm, "
            "minimum_spacing_mm, minimum_drill_mm, copper_to_edge_clearance_mm, "
            "supported_layer_count."
        ),
        read_only=True,
        self_contained=True,
    )


def _render_release_manifest(task: AgentTask) -> str:
    """The release files and their hash, because a hash is not something to guess.

    The gate recomputes this from the files themselves; quoting it here is what
    makes the check answerable rather than a lottery.
    """
    manifest = task.release_manifest
    if not manifest:
        return ""
    files = manifest.get("included_files") or []
    listed = "\n".join(f"  - {path}" for path in files) if isinstance(files, list) else ""
    return (
        "The release package is exactly these files, as they are on disk:\n"
        f"{listed}\n"
        f"Report included_files as that list and release_hash as {manifest.get('release_hash')!r}.\n"
    )


def build_repair_prompt(
    project: ProjectSpec, context: DesignContext, task: AgentTask, inputs: Mapping[str, object]
) -> str:
    """Correct one rejected document rather than write it again from nothing.

    The stage that owns the work has already answered, and a deterministic gate
    has already said what is wrong with the answer. Re-running the stage throws
    that answer away and pays for the whole judgement again; this prompt hands
    the document back with the findings attached and asks for the same document,
    corrected. The repaired document is then put through the very same gate - a
    repair is a proposal like any other, not a bypass.
    """
    rejected = json.dumps(task.rejected_output or {}, default=str, indent=1, sort_keys=True)
    return _prompt(
        "design repair engineer",
        project,
        context,
        task,
        inputs,
        (
            f"A deterministic gate rejected the {task.assigned_agent} stage. Return the same "
            "document, corrected.\n"
            "Change only what the findings require. Keep every part of the document that was "
            "not named by a finding exactly as it is - identifiers, values and wording alike, "
            "because later stages already reference them. Do not add new scope, and do not "
            "delete content to make a finding go away: a requirement, block or part that "
            "belongs in the design still belongs in it after the repair.\n"
            f"{_render_release_manifest(task)}"
            "The rejected document was:\n"
            f"{rejected}"
        ),
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
        (
            "Apply the final release gate and report the package manifest. Never silently "
            "fix design errors.\n"
            f"{_render_release_manifest(task)}"
            "Report every checklist item honestly. An item that is not met is reported as "
            "false and the release_status is needs_human_review; only a release whose every "
            "item is true may be approved. Do not mark an item true to pass the gate."
        ),
        read_only=True,
    )
