"""Team runner contracts with fully offline Devin transport doubles."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.planning.devin import DevinClient, DevinError, SessionHandle, SessionResult
from app.team.evidence import EvidenceStore
from app.team.registry import AGENTS, get_agent
from app.team.runner import DevinAgentRunner, StubAgentRunner
from app.team.schemas import AgentTask, ProjectSpec


def _task(agent_id: str) -> AgentTask:
    return AgentTask(
        task_id=f"T-{agent_id}",
        assigned_agent=agent_id,
        objective="Produce the stage result",
    )


class FakeClient:
    def __init__(self, outputs: list[dict] | None = None) -> None:
        self.outputs = outputs or []
        self.starts: list[dict] = []
        self.waiting = 0
        self.max_waiting = 0
        self.wait_timeouts: list[float | None] = []

    def start(self, prompt: str, title: str, schema: dict, **kwargs: object) -> SessionHandle:
        self.starts.append({"prompt": prompt, "title": title, "schema": schema, **kwargs})
        return SessionHandle(f"session-{len(self.starts)}", f"https://devin/{len(self.starts)}")

    def wait(self, handle: SessionHandle, timeout: float | None = None) -> SessionResult:
        self.wait_timeouts.append(timeout)
        self.waiting += 1
        self.max_waiting = max(self.max_waiting, self.waiting)
        time.sleep(0.01)
        self.waiting -= 1
        output = self.outputs.pop(0) if self.outputs else {}
        return SessionResult(output, handle.url, "running", 0.25, {"status": "running"})


def test_stub_runner_returns_schema_valid_labeled_results(tmp_path: Path) -> None:
    evidence = EvidenceStore("stub-run", tmp_path)
    runner = StubAgentRunner(evidence)
    project = ProjectSpec(project_id="demo", current_stage="team")

    for spec in AGENTS:
        result = runner.run(spec, _task(spec.id), project, None, "stub-rev")
        assert result.status == "stub"
        assert result.runner == "stub"
        assert result.session_url == f"stub://team/{spec.id}"
        assert result.output is not None

    lines = (tmp_path / "team" / "stub-run" / "evidence.jsonl").read_text().splitlines()
    assert len(lines) == len(AGENTS)
    assert all('"runner": "stub"' in line for line in lines)


def test_devin_runner_sends_identity_cost_and_mode() -> None:
    client = FakeClient(
        [
            {
                "project_goal": "goal",
                "workflow": [],
                "status": "ready",
            }
        ]
    )
    runner = DevinAgentRunner(client)
    spec = get_agent("project_manager")
    result = runner.run(
        spec,
        _task(spec.id),
        ProjectSpec(project_id="demo", current_stage="team"),
        None,
        "rev-1",
    )

    assert result.status == "running"
    sent = client.starts[0]
    assert sent["tags"] == ["kicad-mitos", "team", "project_manager"]
    assert sent["max_acu"] == spec.max_acu
    assert sent["devin_mode"] == spec.devin_mode
    assert sent["schema"]["additionalProperties"] is False


def test_each_agent_timeout_is_passed_to_client_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "team_coordination_timeout_seconds", 301.0)
    monkeypatch.setattr(settings, "team_stage_timeout_seconds", 901.0)

    class TimeoutClient(FakeClient):
        def wait(self, handle: SessionHandle, timeout: float | None = None) -> SessionResult:
            self.wait_timeouts.append(timeout)
            raise DevinError(f"session {handle.session_id} timed out")

    for spec in AGENTS:
        client = TimeoutClient()
        invocation = DevinAgentRunner(client).start(
            spec,
            _task(spec.id),
            ProjectSpec(project_id="demo", current_stage="team"),
            None,
            "rev-1",
        )
        DevinAgentRunner(client).wait(invocation)
        expected = 301.0 if spec.id in {"project_manager", "requirements"} else 901.0
        assert client.wait_timeouts == [expected]


def test_team_timeout_defaults_are_resolved_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "team_coordination_timeout_seconds", 302.0)
    monkeypatch.setattr(settings, "team_stage_timeout_seconds", 902.0)
    assert get_agent("project_manager").timeout_seconds == 302.0
    assert get_agent("components").timeout_seconds == 902.0

    monkeypatch.setattr(settings, "team_stage_timeout_seconds", 903.0)
    assert get_agent("components").timeout_seconds == 903.0


def test_timeout_failure_names_timeout_and_session_url() -> None:
    class TimeoutClient(FakeClient):
        def wait(self, handle: SessionHandle, timeout: float | None = None) -> SessionResult:
            self.wait_timeouts.append(timeout)
            raise DevinError(
                f"session {handle.session_id} timed out after {timeout:.0f}s "
                f"while running (session URL: {handle.url})"
            )

    client = TimeoutClient()
    runner = DevinAgentRunner(client)
    result = runner.run(
        get_agent("components"),
        _task("components"),
        ProjectSpec(project_id="demo", current_stage="team"),
        None,
        "rev-1",
    )
    assert result.status == "failed"
    assert "timed out after" in result.unresolved_questions[0]
    assert "https://devin/1" in result.unresolved_questions[0]


def test_devin_runner_posts_team_session_fields_through_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"session_id": "s1", "url": "https://devin/s1"},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "session_id": "s1",
                "status": "running",
                "acus_consumed": 0.5,
                "structured_output": {
                    "project_goal": "goal",
                    "workflow": [],
                    "status": "ready",
                },
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        body = json.dumps(kwargs.get("json", {})).encode()
        headers = kwargs.get("headers", {})
        return transport.handle_request(httpx.Request(method, url, headers=headers, content=body))

    monkeypatch.setattr(httpx, "request", request)
    client = DevinClient(
        api_key="cog-test",
        base_url="https://api.test/v3",
        poll_seconds=0,
        org_id="org-test",
    )
    result = DevinAgentRunner(client).run(
        get_agent("project_manager"),
        _task("project_manager"),
        project=ProjectSpec(project_id="demo", current_stage="team"),
        inputs=None,
        project_version="rev-1",
    )

    assert result.status == "running"
    assert requests[0]["tags"] == ["kicad-mitos", "team", "project_manager"]
    assert requests[0]["max_acu_limit"] == 3
    assert requests[0]["devin_mode"] == "normal"
    assert requests[0]["structured_output_required"] is True


def test_invalid_output_retries_once_then_uses_fallback() -> None:
    client = FakeClient([{"not": "a plan"}, {"still": "not a plan"}])
    runner = DevinAgentRunner(client)
    spec = get_agent("architecture")
    result = runner.run(
        spec,
        _task(spec.id),
        ProjectSpec(project_id="demo", current_stage="team"),
        None,
        "rev-1",
    )

    assert result.status == "fallback"
    assert result.output is not None
    assert len(client.starts) == 2
    assert "Validation errors" in client.starts[1]["prompt"]


def test_http_error_is_a_failed_stage() -> None:
    class ErrorClient(FakeClient):
        def start(self, prompt: str, title: str, schema: dict, **kwargs: object) -> SessionHandle:
            request = httpx.Request("POST", "https://devin/sessions")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("server error", request=request, response=response)

    result = DevinAgentRunner(ErrorClient()).run(
        get_agent("project_manager"),
        _task("project_manager"),
        project=ProjectSpec(project_id="demo", current_stage="team"),
        inputs=None,
        project_version="rev-1",
    )
    assert result.status == "failed"
    assert result.output is None


def test_two_started_sessions_can_wait_concurrently() -> None:
    client = FakeClient(
        [
            {"project_goal": "one", "workflow": [], "status": "ready"},
            {"project_goal": "two", "workflow": [], "status": "ready"},
        ]
    )
    runner = DevinAgentRunner(client)
    spec = get_agent("project_manager")
    project = ProjectSpec(project_id="demo", current_stage="team")
    first = runner.start(spec, _task("one"), project, None, "rev-1")
    second = runner.start(spec, _task("two"), project, None, "rev-1")
    results: list[object] = []

    def wait_for(invocation) -> None:
        results.append(runner.wait(invocation))

    threads = [threading.Thread(target=wait_for, args=(invocation,)) for invocation in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert client.max_waiting == 2
