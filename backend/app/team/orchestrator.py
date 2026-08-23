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
from .registry import AgentSpec, get_agent
from .runner import AgentRunner, DevinAgentRunner
from .schemas import (
    AgentResult,
    AgentTask,
    Architecture,
    CheckFinding,
    ComponentSelection,
    EvidenceRecord,
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


@dataclass(frozen=True)
class OrchestratorOptions:
    project_dir: Path | None = None
    board_path: Path | None = None
    project_state: ProjectState | None = None
    erc_baseline: ErcReport | None = None
    drc_baseline: ErcReport | None = None
    kicad_cli: KicadCli | None = None
    schematic_applier: Callable[[SchematicIntents, Path], None] = apply_schematic_intents


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
        self.events_path = self.evidence.root / "events.jsonl"

    def run(self, project: ProjectSpec) -> TeamRunReport:
        self._latest_erc = self.options.erc_baseline
        self._latest_drc = self.options.drc_baseline
        self._erc_fresh = False
        self._drc_fresh = False
        outputs: dict[str, object] = {}
        results: dict[str, AgentResult] = {}
        gates: dict[str, StageCheckResult] = {}
        returns: dict[str, int] = {}
        rework_findings: dict[str, list[CheckFinding]] = {}
        events: list[dict[str, object]] = []
        queue = list(CANONICAL_ORDER)
        project_version = f"{self.run_id}:0"
        pm_result = self._run_stage(
            get_agent("project_manager"),
            project,
            outputs,
            project_version,
            rework_findings.get("project_manager"),
        )
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
                    ("pcb_layout", "simulation"),
                    project,
                    outputs,
                    project_version,
                    rework_findings,
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
                    if gate.passed:
                        rework_findings.pop(sibling, None)
                    else:
                        target = self._route(sibling, gate)
                        returns[target] = returns.get(target, 0) + 1
                        if returns[target] > 2:
                            self._event(
                                events,
                                "return_trip_cap",
                                {"agent": target, "findings": gate.findings},
                            )
                            return self._report(project, results, gates, events, "needs_human_review")
                        self._event(events, "route_failure", {"from": sibling, "to": target})
                        rework_findings[target] = list(gate.findings)
                        self._enqueue_remainder(queue, target)
                continue
            spec = get_agent(stage)
            result = self._run_stage(
                spec,
                project,
                outputs,
                project_version,
                rework_findings.get(stage),
            )
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
                rework_findings.pop(stage, None)
                continue
            target = self._route(stage, gate)
            returns[target] = returns.get(target, 0) + 1
            if returns[target] > 2:
                self._event(events, "return_trip_cap", {"agent": target, "findings": gate.findings})
                return self._report(project, results, gates, events, "needs_human_review")
            self._event(events, "route_failure", {"from": stage, "to": target})
            rework_findings[target] = list(gate.findings)
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
            reason = result.unresolved_questions[0] if result.unresolved_questions else "agent stage failed"
            return self._failure_gate(stage, reason, project_version)
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
        rework_findings: Mapping[str, list[CheckFinding]] | None = None,
    ) -> dict[str, AgentResult]:
        rework_findings = rework_findings or {}
        if isinstance(self.runner, DevinAgentRunner):
            invocations = {
                stage: self.runner.start(
                    get_agent(stage),
                    self._task(stage),
                    project,
                    self._stage_inputs(get_agent(stage), outputs, rework_findings.get(stage)),
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
                    self._task(stage),
                    project,
                    self._stage_inputs(get_agent(stage), outputs, rework_findings.get(stage)),
                    project_version,
                )
                for stage in stages
            }
            return {stage: future.result() for stage, future in futures.items()}

    def _task(self, stage: str) -> AgentTask:
        spec = get_agent(stage)
        return AgentTask(
            task_id=f"{self.run_id}-{spec.id}",
            assigned_agent=spec.id,
            objective=f"Complete the {spec.name} stage",
        )

    def _run_stage(
        self,
        spec: AgentSpec,
        project: ProjectSpec,
        outputs: Mapping[str, object],
        project_version: str,
        rework_findings: list[CheckFinding] | None = None,
    ) -> AgentResult:
        return self.runner.run(
            spec,
            self._task(spec.id),
            project,
            self._stage_inputs(spec, outputs, rework_findings),
            project_version,
        )

    @staticmethod
    def _stage_inputs(
        spec: AgentSpec,
        outputs: Mapping[str, object],
        rework_findings: list[CheckFinding] | None = None,
    ) -> dict[str, object]:
        inputs = spec.inputs_for(outputs)
        if rework_findings:
            inputs["rework_findings"] = [
                finding.model_dump(mode="json") if isinstance(finding, BaseModel) else finding
                for finding in rework_findings
            ]
        return inputs

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
            return checks.check_simulation(
                output,
                requirements if isinstance(requirements, RequirementsDoc) else None,
                project_version=project_version,
            )
        if stage == "verification" and isinstance(output, VerificationReport):
            return checks.check_verification(output, project_version)
        if stage == "manufacturing" and isinstance(output, ManufacturingReport):
            profile_name = str(project.manufacturer_profile.get("name", ""))
            return checks.check_manufacturing(output, profile_name, project_version)
        if stage == "qa_release" and isinstance(output, ReleaseRecord):
            files = [Path(item) for item in output.included_files]
            return checks.check_qa_release(output, files, project_version)
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
                    schematic_nets=(
                        tuple(read_project(self.options.project_dir).nets)
                        if self.options.project_dir is not None
                        else ()
                    ),
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
        verification_ran = isinstance(verification, VerificationReport) and verification_gate is not None
        verified_passed = 0
        if verification_ran:
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
            requirements_status="passed" if verification_ran else "not run",
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
        normalized = []
        for stage in workflow:
            bare_stage = stage.split(":", 1)[0].strip()
            normalized.append(_ALIASES.get(bare_stage, bare_stage))
        return normalized

    @staticmethod
    def _is_subsequence(workflow: list[str]) -> bool:
        positions = [CANONICAL_ORDER.index(stage) for stage in workflow if stage in CANONICAL_ORDER]
        return len(positions) == len(workflow) and all(
            left < right for left, right in zip(positions, positions[1:], strict=False)
        )

    @staticmethod
    def _route(stage: str, gate: StageCheckResult) -> str:
        text = " ".join(finding.rule.lower() for finding in gate.findings)
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
