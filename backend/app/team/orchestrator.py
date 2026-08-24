"""Deterministic execution of the ten-agent engineering workflow."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from ..config import settings
from ..kicad.checkpoint import Checkpoint
from ..kicad.erc import KicadCli
from ..kicad.reader import ProjectState, read_project
from ..models import ErcReport
from . import checks
from .evidence import EvidenceStore
from .layout import apply_layout
from .profiles import get_profile
from .registry import REPAIR_AGENT_ID, AgentSpec, get_agent, repair_spec
from .runner import AgentRunner, DevinAgentRunner
from .schemas import (
    AgentResult,
    AgentTask,
    CheckFinding,
    Architecture,
    ComponentSelection,
    EvidenceRecord,
    InterventionRequest,
    LayoutProposal,
    ManufacturingReport,
    PMPlan,
    ProjectSpec,
    ReleaseRecord,
    RequirementsDoc,
    SchematicIntents,
    SimulationReport,
    StageCheckResult,
    TeamRunReport,
    VerificationReport,
)
from .schematic import apply_schematic_intents

CANONICAL_ORDER = (
    "project_manager",
    "requirements",
    "architecture",
    "components",
    "schematic_design",
    "pcb_layout",
    "simulation",
    "verification",
    "manufacturing",
    "qa_release",
)
_ALIASES = {"schematic": "schematic_design", "layout": "pcb_layout"}

_MAX_FEEDBACK_FINDINGS = 12
"""How many broken rules a re-run is shown. Enough to fix, short enough to read."""


def _clip(value: object, limit: int = 160) -> str:
    text = repr(value)
    return text if len(text) <= limit else f"{text[:limit]}..."


@dataclass(frozen=True)
class OrchestratorOptions:
    project_dir: Path | None = None
    board_path: Path | None = None
    project_state: ProjectState | None = None
    erc_baseline: ErcReport | None = None
    drc_baseline: ErcReport | None = None
    kicad_cli: KicadCli | None = None
    schematic_applier: Callable[[SchematicIntents, Path], None] = apply_schematic_intents
    intervention: Callable[[InterventionRequest], str | None] | None = None
    """Asked what to do when the gate has beaten the repair stage, or None.

    Returning a non-empty instruction buys the stage one more repair, carrying
    that instruction. Returning None - or not supplying a hook at all, which is
    what every offline path does - leaves the run exactly as it was: the work
    goes back to the agent that owns it, and the return-trip cap still ends on
    `needs_human_review`.
    """


class TeamOrchestrator:
    def __init__(
        self,
        runner: AgentRunner,
        *,
        run_id: str = "team-run",
        workspace_dir: Path | None = None,
        options: OrchestratorOptions | None = None,
    ) -> None:
        self.runner = runner
        self.run_id = run_id
        self.evidence = EvidenceStore(run_id, workspace_dir)
        self.options = options or OrchestratorOptions()
        self._latest_erc = self.options.erc_baseline
        self._latest_drc = self.options.drc_baseline
        self._erc_fresh = False
        self._drc_fresh = False
        self._interventions: set[str] = set()
        self.events_path = self.evidence.root / "events.jsonl"

    def run(self, project: ProjectSpec) -> TeamRunReport:
        self._interventions = set()
        self._latest_erc = self.options.erc_baseline
        self._latest_drc = self.options.drc_baseline
        self._erc_fresh = False
        self._drc_fresh = False
        outputs: dict[str, object] = {}
        results: dict[str, AgentResult] = {}
        gates: dict[str, StageCheckResult] = {}
        returns: dict[str, int] = {}
        # Why each stage was handed back, so a re-run is asked a different question
        # than the one it already failed. Cleared the moment the stage passes.
        feedback: dict[str, list[str]] = {}
        # How many times the repair stage has been spent on each stage.
        repairs: dict[str, int] = {}
        events: list[dict[str, object]] = []
        queue = list(CANONICAL_ORDER)
        project_version = f"{self.run_id}:0"
        pm_result = self._run_stage(get_agent("project_manager"), project, outputs, project_version)
        results["project_manager"] = pm_result
        self._record_result("project_manager", pm_result, project_version)
        if pm_result.output is not None:
            self.evidence.write_stage_output("project_manager", pm_result.output)
        pm_gate = self._pm_gate(pm_result, project_version)
        gates["project_manager"] = pm_gate
        self._write_gate("project_manager", pm_gate)
        if isinstance(pm_result.output, PMPlan):
            proposed = self._normalize_workflow(pm_result.output.workflow)
            if not self._is_subsequence(proposed):
                self._event(
                    events,
                    "invalid_project_manager_workflow",
                    {"workflow": pm_result.output.workflow},
                )
            else:
                self._event(events, "project_manager_workflow_accepted", {"workflow": proposed})
        outputs["project_manager"] = pm_result.output
        queue.pop(0)
        if not pm_gate.passed:
            return self._report(project, results, gates, events, "needs_human_review")

        while queue:
            stage = queue.pop(0)
            if stage == "pcb_layout" and queue and queue[0] == "simulation" and settings.team_parallel:
                queue.pop(0)
                pair = self._run_parallel(
                    ("pcb_layout", "simulation"), project, outputs, project_version, feedback
                )
                for sibling in ("pcb_layout", "simulation"):
                    result = pair[sibling]
                    results[sibling] = result
                    self._record_result(sibling, result, project_version)
                    if self._read_only_violation(get_agent(sibling), result):
                        if result.output is not None:
                            self.evidence.write_stage_output(sibling, result.output)
                        gate = self._failure_gate(
                            sibling,
                            "read-only stage produced a design mutation",
                            project_version,
                        )
                        gates[sibling] = gate
                        self._write_gate(sibling, gate)
                        self._event(events, "read_only_violation", {"stage": sibling})
                        return self._report(project, results, gates, events, "failed")
                    gate = self._evaluate_stage(sibling, result, project, outputs, project_version)
                    gates[sibling] = gate
                    self._write_gate(sibling, gate)
                    repaired = (
                        None
                        if gate.passed
                        else self._repair(
                            sibling, result, gate, project, outputs, project_version, repairs
                        )
                    )
                    if repaired is None and not gate.passed:
                        repaired = self._human_repair(
                            sibling, result, gate, project, outputs, project_version, repairs, events
                        )
                    if repaired is not None:
                        results[sibling], gates[sibling] = repaired
                        results[REPAIR_AGENT_ID], gates[REPAIR_AGENT_ID] = repaired
                        feedback.pop(sibling, None)
                        self._event(events, "stage_repaired", {"stage": sibling})
                    elif gate.passed:
                        feedback.pop(sibling, None)
                    else:
                        target = self._route(sibling, gate)
                        feedback[target] = self._findings_text(sibling, gate)
                        returns[target] = returns.get(target, 0) + 1
                        if returns[target] > 2:
                            self._event(
                                events,
                                "return_trip_cap",
                                {"agent": target, "findings": gate.findings},
                            )
                            return self._report(project, results, gates, events, "needs_human_review")
                        self._event(events, "route_failure", {"from": sibling, "to": target})
                        self._enqueue_remainder(queue, target)
                continue
            spec = get_agent(stage)
            result = self._run_stage(spec, project, outputs, project_version, feedback)
            results[stage] = result
            self._record_result(stage, result, project_version)
            if self._read_only_violation(spec, result):
                if result.output is not None:
                    self.evidence.write_stage_output(stage, result.output)
                gate = self._failure_gate(
                    stage,
                    "read-only stage produced a design mutation",
                    project_version,
                )
                gates[stage] = gate
                self._write_gate(stage, gate)
                self._event(events, "read_only_violation", {"stage": stage})
                return self._report(project, results, gates, events, "failed")
            gate = self._evaluate_stage(stage, result, project, outputs, project_version)
            gates[stage] = gate
            self._write_gate(stage, gate)
            if gate.passed:
                feedback.pop(stage, None)
                continue
            repaired = self._repair(
                stage, result, gate, project, outputs, project_version, repairs
            )
            if repaired is None:
                # The gate beat the repair stage. Before spending a return trip
                # on the agent that owns the work, ask a person what to do.
                repaired = self._human_repair(
                    stage, result, gate, project, outputs, project_version, repairs, events
                )
            if repaired is not None:
                results[stage], gates[stage] = repaired
                results[REPAIR_AGENT_ID], gates[REPAIR_AGENT_ID] = repaired
                feedback.pop(stage, None)
                self._event(events, "stage_repaired", {"stage": stage})
                continue
            target = self._route(stage, gate)
            feedback[target] = self._findings_text(stage, gate)
            returns[target] = returns.get(target, 0) + 1
            if returns[target] > 2:
                self._event(events, "return_trip_cap", {"agent": target, "findings": gate.findings})
                return self._report(project, results, gates, events, "needs_human_review")
            self._event(events, "route_failure", {"from": stage, "to": target})
            self._enqueue_remainder(queue, target)

        release_status = (
            "ready for engineering review"
            if all(gate.passed for gate in gates.values())
            else "needs_human_review"
        )
        return self._report(project, results, gates, events, release_status)

    def _evaluate_stage(
        self,
        stage: str,
        result: AgentResult,
        project: ProjectSpec,
        outputs: dict[str, object],
        project_version: str,
    ) -> StageCheckResult:
        spec = get_agent(stage)
        if self._read_only_violation(spec, result):
            return self._failure_gate(stage, "read-only stage produced a design mutation", project_version)
        if result.output is None:
            return self._failure_gate(stage, "agent stage failed", project_version)
        outputs[stage] = result.output
        self.evidence.write_stage_output(stage, result.output)
        return self._gate(stage, result.output, project, outputs, project_version)

    def _pm_gate(self, result: AgentResult, project_version: str) -> StageCheckResult:
        if not isinstance(result.output, PMPlan):
            return self._failure_gate("project_manager", "project manager output is invalid", project_version)
        if not self._is_subsequence(self._normalize_workflow(result.output.workflow)):
            return StageCheckResult(
                stage="project_manager",
                passed=True,
                findings=[
                    checks.finding(
                        "canonical workflow enforced",
                        result.output.workflow,
                        list(CANONICAL_ORDER),
                        "project_manager",
                        "orchestrator",
                        "warning",
                    )
                ],
                evidence=[],
            )
        return StageCheckResult(
            stage="project_manager",
            passed=True,
            findings=[],
            evidence=[],
        )

    def _run_parallel(
        self,
        stages: tuple[str, str],
        project: ProjectSpec,
        outputs: Mapping[str, object],
        project_version: str,
        feedback: Mapping[str, list[str]] | None = None,
    ) -> dict[str, AgentResult]:
        if isinstance(self.runner, DevinAgentRunner):
            invocations = {
                stage: self.runner.start(
                    get_agent(stage),
                    self._task(stage, feedback),
                    project,
                    get_agent(stage).inputs_for(outputs),
                    project_version,
                )
                for stage in stages
            }
            return {stage: self.runner.wait(invocation) for stage, invocation in invocations.items()}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                stage: executor.submit(
                    self.runner.run,
                    get_agent(stage),
                    self._task(stage, feedback),
                    project,
                    get_agent(stage).inputs_for(outputs),
                    project_version,
                )
                for stage in stages
            }
            return {stage: future.result() for stage, future in futures.items()}

    def _task(self, stage: str, feedback: Mapping[str, list[str]] | None = None) -> AgentTask:
        spec = get_agent(stage)
        return AgentTask(
            task_id=f"{self.run_id}-{spec.id}",
            assigned_agent=spec.id,
            objective=f"Complete the {spec.name} stage",
            prior_gate_findings=list((feedback or {}).get(spec.id, [])),
            release_manifest=self._release_manifest() if stage == "qa_release" else None,
        )

    def _release_files(self) -> list[Path]:
        """The project files a release is made of, as they are on disk right now."""
        if self.options.project_dir is None:
            return []
        return checks.release_files(self.options.project_dir)

    def _release_manifest(self) -> dict[str, object]:
        files = self._release_files()
        return {
            "included_files": [str(path) for path in files],
            "release_hash": checks.release_hash(files),
        }

    @staticmethod
    def _findings_text(stage: str, gate: StageCheckResult) -> list[str]:
        """The failing gate's errors as sentences an agent can act on.

        Bounded on purpose: a finding whose `actual` is a whole acceptance-test
        list would otherwise put a document back into the next prompt, and the
        prompt is what the next session spends its time reading.
        """
        errors = [finding for finding in gate.findings if finding.severity == "error"]
        # One entry per broken rule, not one per object that broke it. A gate
        # that rejects nine tests for the same reason filled the whole feedback
        # budget with near-identical sentences, so the re-run read twelve
        # restatements of two problems, changed nothing, and spent the stage's
        # return trips converging on nothing. The objects still travel with the
        # rule - they are what makes it fixable - just under one heading.
        grouped: dict[str, list[CheckFinding]] = {}
        for item in errors:
            grouped.setdefault(item.rule, []).append(item)
        lines = []
        for rule, items in list(grouped.items())[:_MAX_FEEDBACK_FINDINGS]:
            head = items[0]
            objects = ", ".join(dict.fromkeys(item.kicad_object for item in items))
            more = f" (and {len(items) - 1} more like it)" if len(items) > 1 else ""
            lines.append(
                f"{stage} gate: {rule} - {_clip(objects)} is "
                f"{_clip(head.actual)}, expected {_clip(head.expected)}{more}"
            )
        return lines

    def _run_stage(
        self,
        spec: AgentSpec,
        project: ProjectSpec,
        outputs: Mapping[str, object],
        project_version: str,
        feedback: Mapping[str, list[str]] | None = None,
    ) -> AgentResult:
        return self.runner.run(
            spec, self._task(spec.id, feedback), project, spec.inputs_for(outputs), project_version
        )

    def _repair(
        self,
        stage: str,
        rejected: AgentResult,
        gate: StageCheckResult,
        project: ProjectSpec,
        outputs: dict[str, object],
        project_version: str,
        attempts: dict[str, int],
        guidance: str | None = None,
    ) -> tuple[AgentResult, StageCheckResult] | None:
        """Have the repair stage correct a rejected document, then re-gate it.

        Returns the repaired result and its passing gate, or None when repair is
        off, spent, or produced something the same gate still rejects. The
        caller then falls back to handing the work to the agent that owns it.

        A repair carrying `guidance` is a person's answer being acted on, so it
        is not charged to `team_max_repairs_per_stage`: that budget exists to
        stop the machine retrying itself forever, and this attempt is the one
        thing in the loop that has new information in it.
        """
        if not settings.team_repair or rejected.output is None:
            return None
        if guidance is None:
            if attempts.get(stage, 0) >= settings.team_max_repairs_per_stage:
                return None
            attempts[stage] = attempts.get(stage, 0) + 1
        spec = repair_spec(stage)
        task = AgentTask(
            task_id=f"{self.run_id}-{REPAIR_AGENT_ID}-{stage}",
            assigned_agent=stage,
            objective=f"Correct the rejected {get_agent(stage).name} document",
            prior_gate_findings=self._findings_text(stage, gate),
            rejected_output=rejected.output.model_dump(mode="json")
            if isinstance(rejected.output, BaseModel)
            else None,
            # A repair of the release needs the manifest for the same reason the
            # first attempt did: it cannot compute a hash either. Reaching it
            # through the rejected finding's `expected` field would work only
            # while that field survives the feedback clip.
            release_manifest=self._release_manifest() if stage == "qa_release" else None,
            human_guidance=guidance,
        )
        # Named for the stage it is correcting. The gate that runs afterwards is
        # that stage's own gate, but its record has to say which pass it judged,
        # or a repaired stage is indistinguishable from one that passed first try.
        # A guided pass is named apart from the automatic one for the same reason:
        # it is a different attempt with a different input, and the evidence
        # should not read as though the machine solved it on the second try.
        record_as = f"{REPAIR_AGENT_ID}-{stage}" if guidance is None else f"{REPAIR_AGENT_ID}-human-{stage}"
        result = self.runner.run(spec, task, project, spec.inputs_for(outputs), project_version)
        self._record_result(record_as, result, project_version)
        if result.output is None or self._read_only_violation(spec, result):
            return None
        repaired_gate = self._evaluate_stage(stage, result, project, outputs, project_version)
        self._write_gate(record_as, self._attribute_gate(repaired_gate, record_as))
        return (result, repaired_gate) if repaired_gate.passed else None

    def _human_repair(
        self,
        stage: str,
        rejected: AgentResult,
        gate: StageCheckResult,
        project: ProjectSpec,
        outputs: dict[str, object],
        project_version: str,
        attempts: dict[str, int],
        events: list[dict[str, object]],
    ) -> tuple[AgentResult, StageCheckResult] | None:
        """Ask a person what to do, then let the repair stage act on the answer.

        Reached only once the automatic repair has been tried and the same gate
        has rejected its work too. At that point the machine has said everything
        it has to say - re-running it a third time is how a run burns its return
        trips converging on nothing - so the question goes to someone who can see
        past the document.

        With no hook installed this is a no-op and the caller falls through to
        the return trip exactly as before.
        """
        hook = self.options.intervention
        if hook is None or rejected.output is None:
            return None
        # Once per stage. A stage that fails again after its return trip asks the
        # identical question - the gate has not changed its mind - and a person
        # answering the same thing three times is being made to do the loop's
        # work for it. One answer, then the run ends where it ended before and
        # the finished-run path takes it from there.
        if stage in self._interventions:
            return None
        self._interventions.add(stage)
        findings = self._findings_text(stage, gate)
        request = InterventionRequest(
            run_id=self.run_id,
            stage=stage,
            stage_name=get_agent(stage).name,
            problem=(
                f"The {get_agent(stage).name} stage failed its gate, and the repair stage could "
                f"not satisfy it either. {len(findings)} rule(s) are still broken."
            ),
            findings=findings,
        )
        self._event(events, "human_intervention_requested", {"stage": stage, "findings": findings})
        try:
            answer = hook(request)
        except Exception as exc:  # a person not answering is not a run failure
            self._event(events, "human_intervention_failed", {"stage": stage, "error": str(exc)})
            return None
        guidance = (answer or "").strip()
        if not guidance:
            self._event(events, "human_intervention_declined", {"stage": stage})
            return None
        self._event(events, "human_intervention_answered", {"stage": stage, "answer": guidance})
        repaired = self._repair(
            stage, rejected, gate, project, outputs, project_version, attempts, guidance=guidance
        )
        self._event(
            events,
            "human_repair_passed" if repaired is not None else "human_repair_rejected",
            {"stage": stage},
        )
        return repaired

    @staticmethod
    def _attribute_gate(gate: StageCheckResult, record_as: str) -> StageCheckResult:
        """The same verdict, with its evidence naming the pass it judged."""
        return gate.model_copy(
            update={
                "evidence": [
                    record.model_copy(update={"check": f"team {record_as} gate"})
                    for record in gate.evidence
                ]
            }
        )

    def _gate(
        self,
        stage: str,
        output: object,
        project: ProjectSpec,
        outputs: Mapping[str, object],
        project_version: str,
    ) -> StageCheckResult:
        context = project.design_context
        if stage == "requirements" and isinstance(output, RequirementsDoc):
            return checks.check_requirements(output, project_version)
        if stage == "architecture" and isinstance(output, Architecture):
            requirements = outputs.get("requirements")
            if isinstance(requirements, RequirementsDoc):
                return checks.check_architecture(requirements, output, project_version)
        if stage == "components" and isinstance(output, ComponentSelection):
            return checks.check_components(output, context, project_version=project_version)
        if stage == "simulation" and isinstance(output, SimulationReport):
            requirements = outputs.get("requirements")
            components = outputs.get("components")
            return checks.check_simulation(
                output,
                requirements if isinstance(requirements, RequirementsDoc) else None,
                components if isinstance(components, ComponentSelection) else None,
                project_version=project_version,
            )
        if stage == "verification" and isinstance(output, VerificationReport):
            return checks.check_verification(output, project_version)
        if stage == "manufacturing" and isinstance(output, ManufacturingReport):
            profile_name = str(project.manufacturer_profile.get("name", ""))
            return checks.check_manufacturing(output, profile_name, project_version)
        if stage == "qa_release" and isinstance(output, ReleaseRecord):
            # The files on disk, not the ones the record names: a release that
            # lists a subset of the project would otherwise hash that subset and
            # agree with itself. With no project directory there is nothing to
            # hash, and the gate says so rather than deferring to the record.
            # The DRC report this run produced, so the release checklist's
            # `drc_passes` is checked against the board rather than taken on the
            # stage's word. `_drc_fresh` travels with it because a baseline from
            # before the layout stage says nothing about the board being released.
            return checks.check_qa_release(
                output,
                self._release_files(),
                project_version,
                drc=self._latest_drc,
                drc_fresh=self._drc_fresh,
            )
        if (
            stage == "pcb_layout"
            and isinstance(output, LayoutProposal)
            and self.options.board_path is not None
        ):
            try:
                applied = apply_layout(
                    self.options.board_path,
                    output,
                    get_profile(str(project.manufacturer_profile.get("name", ""))),
                    project_version=project_version,
                    checkpoint_path=self.evidence.root / "checkpoints" / "layout",
                    drc_before=self.options.drc_baseline,
                    cli=self.options.kicad_cli,
                )
                self._latest_drc = applied.drc_report
                self._drc_fresh = True
                return applied.check
            except Exception as exc:
                return self._failure_gate(stage, str(exc), project_version)
        if stage == "schematic_design" and isinstance(output, SchematicIntents):
            return self._schematic_gate(output, project, project_version)
        return self._failure_gate(
            stage, "stage output cannot be grounded by available tools", project_version
        )

    def _schematic_gate(
        self, output: SchematicIntents, project: ProjectSpec, project_version: str
    ) -> StageCheckResult:
        if self.options.project_dir is None:
            return self._failure_gate(
                "schematic_design",
                "schematic applier and project state are unavailable",
                project_version,
            )
        checkpoint = Checkpoint.create(
            self.options.project_dir,
            self.evidence.root / "checkpoints" / "schematic",
        )
        try:
            self.options.schematic_applier(output, self.options.project_dir)
            if self.options.project_state is None or self.options.erc_baseline is None:
                checkpoint.restore()
                return self._failure_gate(
                    "schematic_design", "schematic verification inputs are unavailable", project_version
                )
            after = read_project(self.options.project_dir)
            erc_after = (self.options.kicad_cli or KicadCli(settings.kicad_cli)).run_erc(after.schematic_path)
            self._latest_erc = erc_after
            self._erc_fresh = True
            result = checks.check_schematic(
                self.options.project_state,
                after,
                erc_after,
                erc_baseline=self.options.erc_baseline,
                project_version=project_version,
            )
            if not result.passed:
                checkpoint.restore()
            return result
        except Exception as exc:
            checkpoint.restore()
            return self._failure_gate("schematic_design", str(exc), project_version)

    def _report(
        self,
        project: ProjectSpec,
        results: dict[str, AgentResult],
        gates: dict[str, StageCheckResult],
        events: list[dict[str, object]],
        release_status: str,
    ) -> TeamRunReport:
        outputs = {stage: result.output for stage, result in results.items()}
        requirements = outputs.get("requirements")
        requirement_ids = (
            {requirement.id for requirement in requirements.requirements}
            if isinstance(requirements, RequirementsDoc)
            else set()
        )
        total = len(requirement_ids)
        verification = outputs.get("verification")
        verification_gate = gates.get("verification")
        verified_passed = 0
        if isinstance(verification, VerificationReport) and verification_gate is not None:
            verified_passed = len(
                {
                    outcome.requirement_id
                    for outcome in verification.requirement_outcomes or []
                    if outcome.requirement_id in requirement_ids
                    and outcome.status == "passed"
                    and bool(outcome.evidence)
                }
            )
        simulation = outputs.get("simulation")
        simulation_tests = simulation.tests if isinstance(simulation, SimulationReport) else []
        power_tests_total = len(simulation_tests)
        power_tests_passed = sum(test.status.lower() in {"pass", "passed", "ok"} for test in simulation_tests)
        if gates.get("simulation") is None or not gates["simulation"].passed:
            power_tests_passed = 0
        report = TeamRunReport(
            run_id=self.run_id,
            project=project,
            request=project.request,
            per_stage_results=results,
            requirements_satisfied=verified_passed,
            requirements_total=total,
            requirements_status="passed" if isinstance(requirements, RequirementsDoc) else "not run",
            erc_status=self._tool_status(self._latest_erc, self._erc_fresh, "schematic"),
            drc_status=self._tool_status(self._latest_drc, self._drc_fresh, "layout"),
            power_tests=power_tests_total,
            power_tests_passed=power_tests_passed,
            power_tests_total=power_tests_total,
            power_tests_status="passed" if isinstance(simulation, SimulationReport) else "not run",
            manufacturing_warnings=sum(
                finding.severity == "warning"
                for gate in gates.values()
                for finding in gate.findings
                if gate.stage == "manufacturing"
            ),
            manufacturing_status="passed" if "manufacturing" in outputs else "not run",
            critical_issues=sum(
                finding.severity == "error" for gate in gates.values() for finding in gate.findings
            ),
            open_critical_findings=[
                finding.model_dump(mode="json")
                for gate in gates.values()
                for finding in gate.findings
                if finding.severity == "error"
            ],
            release_status=release_status,
            summary=release_status,
        )
        (self.evidence.root / "gates.json").write_text(
            json.dumps({key: value.model_dump(mode="json") for key, value in gates.items()}, indent=2) + "\n"
        )
        self.events_path.write_text("".join(json.dumps(event, sort_keys=True) + "\n" for event in events))
        (self.evidence.root / "final-report.json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2) + "\n"
        )
        return report

    def _write_gate(self, stage: str, gate: StageCheckResult) -> None:
        (self.evidence.root / f"{stage}-gate.json").write_text(
            json.dumps(gate.model_dump(mode="json"), indent=2) + "\n"
        )
        for record in gate.evidence:
            self.evidence.append(record)

    def _record_result(self, stage: str, result: AgentResult, project_version: str) -> None:
        self.evidence.append(
            EvidenceRecord(
                check=f"team agent {stage}",
                tool="team orchestrator",
                project_version=project_version,
                timestamp=datetime.now(timezone.utc).isoformat(),
                result=result.status,
                findings=result.unresolved_questions,
                runner=result.runner,
                session_url=result.session_url,
                status=result.status,
                acus_consumed=result.acus_consumed,
                duration_seconds=result.duration_seconds,
            )
        )

    def _failure_gate(self, stage: str, message: str, project_version: str) -> StageCheckResult:
        return StageCheckResult(
            stage=stage,
            passed=False,
            findings=[
                checks.finding(
                    "stage grounded and accepted",
                    message,
                    "accepted deterministic evidence",
                    stage,
                    "orchestrator",
                    "error",
                )
            ],
            evidence=[],
        )

    def _event(self, events: list[dict[str, object]], event: str, detail: dict[str, object]) -> None:
        events.append({"event": event, **{key: self._jsonable(value) for key, value in detail.items()}})

    @classmethod
    def _jsonable(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, list):
            return [cls._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): cls._jsonable(item) for key, item in value.items()}
        return value

    @staticmethod
    def _enqueue_remainder(queue: list[str], target: str) -> None:
        if target not in CANONICAL_ORDER:
            return
        start = CANONICAL_ORDER.index(target)
        queue[:] = list(dict.fromkeys([*CANONICAL_ORDER[start:], *queue]))

    @staticmethod
    def _normalize_workflow(workflow: list[str]) -> list[str]:
        return [_ALIASES.get(stage, stage) for stage in workflow]

    @staticmethod
    def _is_subsequence(workflow: list[str]) -> bool:
        positions = [CANONICAL_ORDER.index(stage) for stage in workflow if stage in CANONICAL_ORDER]
        return len(positions) == len(workflow) and all(
            left < right for left, right in zip(positions, positions[1:], strict=False)
        )

    @staticmethod
    def _route(stage: str, gate: StageCheckResult) -> str:
        text = " ".join(finding.rule.lower() for finding in gate.findings)
        # Stages that own their own failures are named before any keyword is read.
        # Verification's rules are *about* requirements - "a failing verification
        # is explained by a blocking finding" - and the keyword pass below would
        # hand its failure to the requirements stage, replaying the whole pipeline
        # to fix a document verification wrote. That is the trap commit 1e22e39
        # closed once already; a rule name must not be able to reopen it.
        if stage == "verification":
            return "verification"
        if stage == "requirements" or "requirement" in text:
            return "requirements"
        if stage == "architecture":
            return "architecture"
        if stage == "components" or "part" in text or "symbol" in text:
            return "components"
        if "erc" in text or "connection" in text:
            return "schematic_design"
        if stage == "manufacturing":
            if "profile" in text:
                return "manufacturing"
            if any(term in text for term in ("part", "footprint", "component")):
                return "components"
            if any(term in text for term in ("trace", "clearance", "drill", "placement")):
                return "pcb_layout"
            return "manufacturing"
        if stage == "pcb_layout" or "clearance" in text or "placement" in text:
            return "pcb_layout"
        if stage == "simulation":
            return "schematic_design"
        if stage == "qa_release":
            return "qa_release"
        return stage

    @staticmethod
    def _read_only_violation(spec: AgentSpec, result: AgentResult) -> bool:
        if not spec.read_only or result.output is None:
            return False
        if isinstance(result.output, dict):
            return "actions" in result.output or "placements" in result.output
        return isinstance(result.output, (SchematicIntents, LayoutProposal))

    @staticmethod
    def _tool_status(report: ErcReport | None, fresh: bool, stage: str) -> str:
        if report is None:
            return "not run"
        has_errors = report.errors > 0 or any(
            violation.severity == "error" for violation in report.violations
        )
        if not fresh:
            baseline_status = "passed" if report.ran and not has_errors else "failed"
            return f"{baseline_status} (baseline; {stage} stage did not run)"
        if not report.ran:
            return "not run"
        return "passed" if not has_errors else "failed"


def run_team(
    project: ProjectSpec,
    runner: AgentRunner,
    *,
    run_id: str = "team-run",
    workspace_dir: Path | None = None,
    options: OrchestratorOptions | None = None,
) -> TeamRunReport:
    return TeamOrchestrator(
        runner,
        run_id=run_id,
        workspace_dir=workspace_dir,
        options=options,
    ).run(project)
