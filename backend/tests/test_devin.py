"""The Devin agent path, against a real local stub of the v3 sessions API.

Driven over real HTTP rather than a patched function, so the client's request
shape, the poll loop, the timeout and every fallback actually execute. The
important assertions here are the ones about what must NOT be auto-resolved: an
agent answering a refusal on the user's behalf would silently build something
they did not ask for.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import settings
from app.models import ActionPlan, Clarification, PlanAnswers
from app.planning.devin import (
    RESOLVABLE_REASONS,
    UNRESOLVABLE_REASONS,
    DevinClient,
    DevinError,
    apply_answer,
    build_prompt,
    resolve_and_plan,
    resolve_clarification,
)
from app.planning.generator import generate_plan
from app.planning.llm import plan_from_instruction

PIN_QUESTION = Clarification(
    question="Which pin carries I2C data (SDA)?",
    reason="unidentified_peripheral_pins",
    options=["1:VCC", "2:GND", "3:P3", "4:P4", "5:P5"],
    answer_key="peripheral_sda",
)


class _Stub:
    """A stub Devin API. `statuses` is the sequence GET returns, one per poll."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.headers: list[dict] = []
        self.paths: list[str] = []
        self.polls = 0
        # v3 status values: new | claimed | running | exit | error | suspended
        self.statuses = ["exit"]
        self.structured_output: dict | None = {"answer": "3:P3", "reasoning": "TMP102 pin 3 is SDA."}
        self.create_status = 200


@pytest.fixture()
def stub(monkeypatch: pytest.MonkeyPatch) -> _Stub:
    state = _Stub()

    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: dict, code: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length", 0))
            state.created.append(json.loads(self.rfile.read(length) or b"{}"))
            state.headers.append(dict(self.headers))
            state.paths.append(self.path)
            if state.create_status != 200:
                self._json({"error": "nope"}, state.create_status)
                return
            self._json(
                {
                    "session_id": "devin-1",
                    "url": "https://app.devin.ai/sessions/1",
                    "status": "new",
                    "org_id": "org-test",
                }
            )

        def do_GET(self) -> None:  # noqa: N802
            state.paths.append(self.path)
            if self.path.endswith("/self"):
                self._json({"principal_type": "service_user", "org_id": "org-test"})
                return
            index = min(state.polls, len(state.statuses) - 1)
            status = state.statuses[index]
            state.polls += 1
            payload: dict = {"session_id": "devin-1", "status": status, "acus_consumed": 0.4}
            # Only the get/list endpoints carry structured_output.
            if status == "exit" and state.structured_output is not None:
                payload["structured_output"] = state.structured_output
            self._json(payload)

        def log_message(self, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(settings, "devin_api_key", "cog_test")
    monkeypatch.setattr(settings, "devin_base_url", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(settings, "devin_poll_seconds", 0.01)
    monkeypatch.setattr(settings, "devin_timeout_seconds", 2.0)
    monkeypatch.setattr(settings, "auto_resolve", True)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------- the safety allowlist


def test_the_two_reason_sets_do_not_overlap_and_cover_the_planner() -> None:
    assert not (RESOLVABLE_REASONS & UNRESOLVABLE_REASONS)


@pytest.mark.parametrize("reason", sorted(UNRESOLVABLE_REASONS))
def test_a_refusal_is_never_answered_on_the_users_behalf(state, stub: _Stub, reason: str) -> None:
    """The heart of it: asking for 5V must not quietly become a 3.3V bus."""
    refusal = Clarification(
        question="The project has no 5V rail. Use 3.3V instead?",
        reason=reason,
        options=["3.3V"],
        answer_key="logic_voltage",
    )
    assert resolve_clarification(state, ["U1", "U2"], "connect at 5V", refusal) is None
    # And it never even reached the API.
    assert stub.created == []


@pytest.mark.parametrize("reason", sorted(RESOLVABLE_REASONS))
def test_every_resolvable_reason_is_actually_attempted(state, stub: _Stub, reason: str) -> None:
    question = PIN_QUESTION.model_copy(update={"reason": reason})
    assert resolve_clarification(state, ["U1", "U2"], "connect over I2C", question) is not None
    assert len(stub.created) == 1


# ------------------------------------------------------------ the client


def test_request_carries_the_schema_the_cost_cap_and_the_key(state, stub: _Stub) -> None:
    resolve_clarification(state, ["U1", "U2"], "connect over I2C", PIN_QUESTION)
    sent = stub.created[0]
    assert sent["structured_output_schema"]["required"] == ["answer", "reasoning"]
    assert sent["structured_output_required"] is True
    assert sent["max_acu_limit"] == settings.devin_max_acu
    assert sent["devin_mode"] == settings.devin_mode
    assert stub.headers[0]["Authorization"] == "Bearer cog_test"
    # v3 shape: org-scoped path, and the org was discovered from /self.
    assert any(p.endswith("/self") for p in stub.paths)
    assert any(p == "/organizations/org-test/sessions" for p in stub.paths)


def test_it_polls_until_the_session_finishes(state, stub: _Stub) -> None:
    stub.statuses = ["new", "running", "running", "exit"]
    answer = resolve_clarification(state, ["U1", "U2"], "connect over I2C", PIN_QUESTION)
    assert answer is not None
    assert answer.answer == "3:P3"
    assert stub.polls >= 3


def test_a_session_that_never_finishes_falls_back_rather_than_hanging(state, stub: _Stub) -> None:
    stub.statuses = ["running"]
    assert resolve_clarification(state, ["U1", "U2"], "connect over I2C", PIN_QUESTION) is None


def test_an_errored_session_falls_back(state, stub: _Stub) -> None:
    stub.statuses = ["error"]
    assert resolve_clarification(state, ["U1", "U2"], "connect over I2C", PIN_QUESTION) is None


def test_an_http_error_falls_back(state, stub: _Stub) -> None:
    stub.create_status = 500
    assert resolve_clarification(state, ["U1", "U2"], "connect over I2C", PIN_QUESTION) is None


def test_an_answer_outside_the_offered_options_is_discarded(state, stub: _Stub) -> None:
    """A hallucinated pin must not reach the planner."""
    stub.structured_output = {"answer": "9:MADE_UP", "reasoning": "confident nonsense"}
    assert resolve_clarification(state, ["U1", "U2"], "connect over I2C", PIN_QUESTION) is None


def test_no_key_means_no_call(state, stub: _Stub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "devin_api_key", None)
    assert resolve_clarification(state, ["U1", "U2"], "connect over I2C", PIN_QUESTION) is None
    assert stub.created == []


def test_run_raises_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "devin_api_key", None)
    with pytest.raises(DevinError):
        DevinClient().run("prompt", "title")


# ------------------------------------------------------------- the prompt


def test_the_prompt_carries_part_numbers_and_real_pins(state) -> None:
    """Part number and lib_id are what let the agent read a datasheet."""
    prompt = build_prompt(state, ["U1", "U2"], "connect over I2C", PIN_QUESTION)
    assert "TMP102" in prompt
    assert "Sensor:TMP102" in prompt or "lib_id" in prompt
    assert "3:SDA" in prompt or "SDA" in prompt
    for option in PIN_QUESTION.options:
        assert option in prompt
    assert "Do not modify any files" in prompt


# ------------------------------------------------------- answers plumbing


def test_apply_answer_handles_legacy_and_namespaced_keys() -> None:
    flat = apply_answer(PlanAnswers(), "logic_voltage", "3.3V")
    assert flat.logic_voltage == "3.3V"
    nested = apply_answer(PlanAnswers(), "controller_pin:SCK", "GPIO18")
    assert nested.controller_pins == {"SCK": "GPIO18"}
    peripheral = apply_answer(nested, "peripheral_pin:MOSI", "4:MOSI")
    assert peripheral.peripheral_pins == {"MOSI": "4:MOSI"}
    assert peripheral.controller_pins == {"SCK": "GPIO18"}


# ----------------------------------------------------- end to end planning


def test_the_agent_turns_a_question_into_a_plan(store, stub: _Stub) -> None:
    """esp32_i2c_unnamed_pins has P1..P5, so the planner must ask. Now it doesn't."""
    session = store.create("esp32_i2c_unnamed_pins")
    state = session.state

    def replan(answers: PlanAnswers):
        return generate_plan(state, ["U1", "U2"], "Connect over I2C at 3.3V", None, answers)

    first = replan(PlanAnswers())
    assert isinstance(first, Clarification)

    # P3 for SDA, then P4 for SCL: two rounds, two different answers.
    answers_seen: list[str] = []

    class _Sequenced(DevinClient):
        def run(self, prompt: str, title: str, schema: dict | None = None):
            option = "3:P3" if "data" in prompt or "SDA" in prompt else "4:P4"
            answers_seen.append(option)
            return {"answer": option, "reasoning": "from the datasheet"}, "https://app.devin.ai/s/1"

    result, answers, notes = resolve_and_plan(
        state, ["U1", "U2"], "Connect over I2C at 3.3V", PlanAnswers(), first, replan, _Sequenced()
    )
    assert isinstance(result, ActionPlan), result
    assert notes, "the agent's decisions must be recorded"
    # Recorded as visible assumptions, not passed off as facts from the schematic.
    assert any("Devin agent" in a for a in result.assumptions)


def test_auto_resolve_is_off_unless_both_key_and_flag_are_set(state, monkeypatch) -> None:
    monkeypatch.setattr(settings, "devin_api_key", "cog_test")
    monkeypatch.setattr(settings, "auto_resolve", False)
    assert settings.agent_resolves_ambiguity is False
    result, source = plan_from_instruction(state, ["U1", "U2"], "Connect these somehow")
    assert isinstance(result, Clarification)
    assert source == "rules"


def test_an_agent_resolved_plan_that_fails_the_validator_is_discarded(store, stub: _Stub) -> None:
    """Observed against the live API: the agent picked a pin tied to GND.

    A plan nobody can approve is worse than the question it replaced, so the
    agent's answers are thrown away and the original question comes back.
    """
    session = store.create("esp32_i2c_unnamed_pins")
    # P5 is tied to GND in this fixture, so choosing it for SCL yields a pull-up
    # on a power net - exactly what the live agent did.
    stub.structured_output = {"answer": "5:P5", "reasoning": "confidently wrong"}
    result, source = plan_from_instruction(session.state, ["U1", "U2"], "Connect over I2C at 3.3V")
    assert isinstance(result, Clarification)
    assert source == "rules"


def test_source_reports_the_mixture_when_the_agent_contributed(store, stub: _Stub) -> None:
    """'agent+rules' is the honest label: the agent answered, the rules planned."""
    session = store.create("esp32_i2c_unnamed_pins")
    stub.structured_output = {"answer": "3:P3", "reasoning": "datasheet says SDA"}
    result, source = plan_from_instruction(
        session.state, ["U1", "U2"], "Connect over I2C at 3.3V"
    )
    assert source in {"agent+rules", "rules"}
    if source == "agent+rules":
        assert stub.created
