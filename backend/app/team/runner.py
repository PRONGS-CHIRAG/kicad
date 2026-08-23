"""Execution adapters for real Devin sessions and deterministic offline runs."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from ..planning.devin import DevinClient, DevinError, SessionHandle
from .evidence import EvidenceStore
from .fallbacks import fallback_for
from .registry import AgentSpec
from .schemas import AgentResult, AgentTask, EvidenceRecord, ProjectSpec, json_schema


class AgentRunner(Protocol):
    def run(
        self,
        spec: AgentSpec,
        task: AgentTask,
        project: ProjectSpec,
        inputs: Mapping[str, object] | None,
        project_version: str,
    ) -> AgentResult: ...


@dataclass
class DevinInvocation:
    spec: AgentSpec
    task: AgentTask
    project: ProjectSpec
    inputs: Mapping[str, object]
    project_version: str
    prompt: str
    handle: SessionHandle
    started_at: float


class DevinAgentRunner:
    runner_name = "devin"

    def __init__(self, client: DevinClient | None = None, evidence: EvidenceStore | None = None) -> None:
        self.client = client or DevinClient()
        self.evidence = evidence

    def start(
        self,
        spec: AgentSpec,
        task: AgentTask,
        project: ProjectSpec,
        inputs: Mapping[str, object] | None,
        project_version: str,
    ) -> DevinInvocation:
        declared_inputs = inputs or {}
        prompt = spec.build_prompt(project, task, declared_inputs)
        handle = self.client.start(
            prompt,
            f"KiCAD Mitos team: {spec.name}",
            json_schema(spec.output_model),
            tags=["kicad-mitos", "team", spec.id],
            max_acu=spec.max_acu,
            devin_mode=spec.devin_mode,
        )
        return DevinInvocation(
            spec=spec,
            task=task,
            project=project,
            inputs=declared_inputs,
            project_version=project_version,
            prompt=prompt,
            handle=handle,
            started_at=time.perf_counter(),
        )

    def wait(self, invocation: DevinInvocation, timeout: float | None = None) -> AgentResult:
        try:
            session = self.client.wait(invocation.handle, timeout)
        except (DevinError, httpx.HTTPError) as exc:
            return self._finish(invocation, "failed", None, 0.0, str(exc))
        try:
            output = invocation.spec.output_model.model_validate(session.output)
        except ValidationError as first_error:
            retry_prompt = (
                f"{invocation.prompt}\n\nYour previous structured output was invalid. "
                "Return the complete corrected object only. Validation errors:\n"
                f"{first_error}"
            )
            try:
                retry_handle = self.client.start(
                    retry_prompt,
                    f"KiCAD Mitos team: {invocation.spec.name} (retry)",
                    json_schema(invocation.spec.output_model),
                    tags=["kicad-mitos", "team", invocation.spec.id],
                    max_acu=invocation.spec.max_acu,
                    devin_mode=invocation.spec.devin_mode,
                )
                retry_session = self.client.wait(retry_handle, timeout)
                output = invocation.spec.output_model.model_validate(retry_session.output)
                return self._finish(
                    invocation,
                    retry_session.status,
                    output,
                    session.acus_consumed + retry_session.acus_consumed,
                )
            except (DevinError, httpx.HTTPError, ValidationError) as exc:
                if invocation.spec.fallback is not None:
                    output = invocation.spec.fallback(
                        invocation.project,
                        invocation.task,
                        invocation.inputs,
                    )
                    return self._finish(
                        invocation,
                        "fallback",
                        output,
                        session.acus_consumed,
                        str(exc),
                    )
                return self._finish(invocation, "failed", None, session.acus_consumed, str(exc))
        return self._finish(invocation, session.status, output, session.acus_consumed)

    def run(
        self,
        spec: AgentSpec,
        task: AgentTask,
        project: ProjectSpec,
        inputs: Mapping[str, object] | None,
        project_version: str,
    ) -> AgentResult:
        try:
            invocation = self.start(spec, task, project, inputs, project_version)
        except (DevinError, httpx.HTTPError) as exc:
            result = AgentResult(
                task_id=task.task_id,
                agent=spec.id,
                status="failed",
                runner=self.runner_name,
                unresolved_questions=[str(exc)],
                duration_seconds=0.0,
            )
            self._record(spec, result, project_version, str(exc))
            return result
        return self.wait(invocation)

    def _finish(
        self,
        invocation: DevinInvocation,
        status: str,
        output: BaseModel | None,
        acus_consumed: float,
        error: str | None = None,
    ) -> AgentResult:
        result = AgentResult(
            task_id=invocation.task.task_id,
            agent=invocation.spec.id,
            status=status,
            output=output,
            runner=self.runner_name,
            session_url=invocation.handle.url,
            acus_consumed=acus_consumed,
            duration_seconds=time.perf_counter() - invocation.started_at,
            unresolved_questions=[error] if error else [],
        )
        self._record(invocation.spec, result, invocation.project_version, error)
        return result

    def _record(
        self,
        spec: AgentSpec,
        result: AgentResult,
        project_version: str,
        error: str | None,
    ) -> None:
        if self.evidence is None:
            return
        self.evidence.append(
            EvidenceRecord(
                check=f"team agent {spec.id}",
                tool="Devin v3",
                project_version=project_version,
                timestamp=datetime.now(timezone.utc).isoformat(),
                result=result.status,
                findings=[error] if error else [],
                runner=self.runner_name,
                session_url=result.session_url,
                status=result.status,
                acus_consumed=result.acus_consumed,
                duration_seconds=result.duration_seconds,
            )
        )
        if result.output is not None:
            self.evidence.write_stage_output(spec.id, result.output)


class StubAgentRunner:
    runner_name = "stub"

    def __init__(
        self,
        evidence: EvidenceStore | None = None,
        outputs: Mapping[str, BaseModel] | None = None,
    ) -> None:
        self.evidence = evidence
        self.outputs = dict(outputs or {})

    def run(
        self,
        spec: AgentSpec,
        task: AgentTask,
        project: ProjectSpec,
        inputs: Mapping[str, object] | None,
        project_version: str,
    ) -> AgentResult:
        output = self.outputs.get(spec.id) or fallback_for(spec.id, project, task, inputs or {})
        result = AgentResult(
            task_id=task.task_id,
            agent=spec.id,
            status="stub",
            output=output,
            runner=self.runner_name,
            session_url=f"stub://team/{spec.id}",
            duration_seconds=0.0,
        )
        if self.evidence is not None:
            self.evidence.append(
                EvidenceRecord(
                    check=f"team agent {spec.id}",
                    tool="StubAgentRunner",
                    project_version=project_version,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    result="stub",
                    findings=["deterministic offline output"],
                    runner=self.runner_name,
                    session_url=result.session_url,
                    status=result.status,
                    duration_seconds=result.duration_seconds,
                )
            )
            self.evidence.write_stage_output(spec.id, output)
        return result
