"""The optional LLM planning path, against a real local HTTP stub.

These tests exist because the claim "the model only proposes, and its plan is
discarded if the validator rejects it" was previously never executed: no test
touched `app.planning.llm`, and no API key is configured in any environment the
suite runs in. The stub is a real HTTP server rather than a patched function, so
the request construction and response parsing in `_call_openai` run for real.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import settings
from app.models import ActionPlan, Clarification, PlanAnswers
from app.planning.llm import plan_from_instruction

INSTRUCTION = "Connect U1 and U2 using I2C with 3.3 V logic."

VALID_PLAN = {
    "plan": {
        "schema_version": "1.0",
        "goal": "Connect U2 to U1 using I2C",
        "selected_components": ["U1", "U2"],
        "protocol": "I2C",
        "logic_voltage": "3.3V",
        "assumptions": ["U1 is the controller"],
        "warnings": [],
        "protected_objects": [],
        "actions": [
            {
                "id": "action-1",
                "type": "connect_pins",
                "from": "U2.SDA",
                "to": "U1.GPIO21",
                "net_name": "I2C_SDA",
                "purpose": "I2C data",
            }
        ],
    }
}

# Same shape, but names a pin that does not exist on the symbol. The validator
# must catch this and the deterministic planner must be used instead.
PLAN_WITH_BAD_PIN = json.loads(json.dumps(VALID_PLAN))
PLAN_WITH_BAD_PIN["plan"]["actions"][0]["from"] = "U2.NO_SUCH_PIN"


class _Stub:
    """A one-endpoint OpenAI-compatible stub that records what it was sent."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.status = 200
        self.body: str | dict = VALID_PLAN

    def payload(self) -> bytes:
        # A str body is the malformed-content case: sent through verbatim.
        content = self.body if isinstance(self.body, str) else json.dumps(self.body)
        return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


@pytest.fixture()
def stub(monkeypatch: pytest.MonkeyPatch) -> _Stub:
    state = _Stub()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length", 0))
            state.requests.append(json.loads(self.rfile.read(length) or b"{}"))
            state.headers.append(dict(self.headers))
            if state.status != 200:
                self.send_response(state.status)
                self.end_headers()
                self.wfile.write(b'{"error":"boom"}')
                return
            body = state.payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # keep test output quiet
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_base_url", f"http://127.0.0.1:{server.server_port}/v1")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def test_valid_llm_plan_is_used(state, stub: _Stub) -> None:
    stub.body = VALID_PLAN
    plan, source = plan_from_instruction(state, ["U1", "U2"], INSTRUCTION)
    assert source == "llm"
    assert isinstance(plan, ActionPlan)
    assert [a.id for a in plan.actions] == ["action-1"]
    assert plan.actions[0].from_pin == "U2.SDA"


def test_request_is_shaped_for_structured_output(state, stub: _Stub) -> None:
    plan_from_instruction(state, ["U1", "U2"], INSTRUCTION)
    assert len(stub.requests) == 1
    sent = stub.requests[0]
    assert sent["temperature"] == 0
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["model"] == settings.llm_model
    assert stub.headers[0]["Authorization"] == "Bearer test-key"
    # The project state must reach the model, or it would be inventing pins.
    assert "U1" in sent["messages"][1]["content"]


def test_plan_failing_the_validator_is_discarded(state, stub: _Stub) -> None:
    """The whole point: a model plan that does not validate is thrown away."""
    stub.body = PLAN_WITH_BAD_PIN
    plan, source = plan_from_instruction(state, ["U1", "U2"], INSTRUCTION)
    assert source == "rules"
    assert isinstance(plan, ActionPlan)
    # The deterministic plan is a real one, not the model's rejected suggestion.
    assert all("NO_SUCH_PIN" not in a.from_pin for a in plan.actions if hasattr(a, "from_pin"))
    assert len(plan.actions) > 1


def test_llm_clarification_is_passed_through(state, stub: _Stub) -> None:
    stub.body = {
        "clarification": {
            "question": "Which interface?",
            "reason": "protocol_not_specified",
            "options": ["I2C"],
        }
    }
    result, source = plan_from_instruction(state, ["U1", "U2"], INSTRUCTION)
    assert source == "llm"
    assert isinstance(result, Clarification)
    assert result.reason == "protocol_not_specified"


def test_http_error_falls_back_to_rules(state, stub: _Stub) -> None:
    stub.status = 500
    plan, source = plan_from_instruction(state, ["U1", "U2"], INSTRUCTION)
    assert source == "rules"
    assert isinstance(plan, ActionPlan)


def test_malformed_model_output_falls_back_to_rules(state, stub: _Stub) -> None:
    stub.body = "this is not json"
    plan, source = plan_from_instruction(state, ["U1", "U2"], INSTRUCTION)
    assert source == "rules"
    assert isinstance(plan, ActionPlan)


def test_answered_clarifications_skip_the_model_entirely(state, stub: _Stub) -> None:
    """Once the user has answered, the answer is honoured literally, not re-interpreted."""
    plan, source = plan_from_instruction(
        state, ["U1", "U2"], INSTRUCTION, PlanAnswers(logic_voltage="3.3V")
    )
    assert source == "rules"
    assert stub.requests == []


def test_no_api_key_never_calls_out(state, stub: _Stub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", None)
    plan, source = plan_from_instruction(state, ["U1", "U2"], INSTRUCTION)
    assert source == "rules"
    assert stub.requests == []
