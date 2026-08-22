"""Let a real Devin agent settle an ambiguity instead of asking the user.

The deterministic planner refuses to guess: when the schematic does not say which
pin carries SDA, it returns a `Clarification` and stops. That is safe but it puts
a question in front of the user. This module hands the question to a Devin session
instead, so planning thinks for itself and continues.

The split of labour is deliberate, and it is what keeps the safety story intact:

  agent  -> resolves an ambiguity, one field at a time, from the real project state
  rules  -> build the plan, validate it, and decide whether to keep it

The agent never writes an action, never sees the validator and has no say in the
accept/reject decision. Everything it answers is recorded as a visible assumption
on the plan, and every answer must be one of the options the planner itself
offered — an answer outside that set is discarded and the question goes back to
the user.

## What must never be auto-resolved

Not every clarification is an ambiguity. Three of them are *refusals on electrical
grounds*, and answering those on the user's behalf would silently build something
they did not ask for — a 5 V request quietly becoming a 3.3 V bus. Plan §21 is
explicit that rejecting an uncertain instruction beats silently producing a wrong
connection, so the resolvable reasons are an allowlist: a reason code added later
is unresolvable until someone deliberately says otherwise.

API: the v3 organization endpoints. `cog_` service-user keys work only with
v3 — v1/v2 are for the legacy `apk_` keys and return 403 to a service user.
  POST /v3/organizations/{org_id}/sessions
  GET  /v3/organizations/{org_id}/sessions/{devin_id}
  GET  /v3/self                              (discovers org_id)
https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx

from ..config import settings
from ..kicad.reader import ProjectState
from ..models import ActionPlan, Clarification, PlanAnswers

logger = logging.getLogger(__name__)

# Ambiguities: the user's intent is clear, only a detail is missing.
RESOLVABLE_REASONS = frozenset(
    {
        "protocol_not_specified",
        "voltage_not_specified",
        "unidentified_peripheral_pins",
        "controller_pin_unresolved",
    }
)

# Kept out on purpose, with the reason, so nobody widens this by accident:
#   incompatible_voltage, incompatible_logic_voltage, unsupported_protocol
#       -> refusals on electrical grounds. Answering them changes what the user
#          asked for rather than filling in a blank.
#   unknown_component, insufficient_selection, ambiguous_roles
#       -> these pick *which components get connected*. That is the user's
#          intent, not a detail, and their answer_key is "selection", which only
#          the UI knows how to apply.
UNRESOLVABLE_REASONS = frozenset(
    {
        "incompatible_voltage",
        "incompatible_logic_voltage",
        "unsupported_protocol",
        "unknown_component",
        "insufficient_selection",
        "ambiguous_roles",
    }
)

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "Exactly one of the offered options, copied verbatim.",
        },
        "reasoning": {
            "type": "string",
            "description": "One sentence on why, citing the part or the datasheet.",
        },
    },
    "required": ["answer", "reasoning"],
    "additionalProperties": False,
}

MAX_ROUNDS = 4
"""A plan can need several fields resolved; this bounds the agent round trips."""


class DevinError(RuntimeError):
    pass


@dataclass
class AgentAnswer:
    answer: str
    reasoning: str
    session_url: str


class DevinClient:
    """The Devin v3 organization sessions API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        poll_seconds: float | None = None,
        org_id: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.devin_api_key
        self.base_url = (base_url or settings.devin_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.devin_timeout_seconds
        self.poll_seconds = poll_seconds or settings.devin_poll_seconds
        self._org_id = org_id or settings.devin_org_id
        self.connect_budget_seconds = 90.0
        """How long to keep retrying a transport error before giving up."""

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Retry transport errors with backoff.

        Name resolution is not reliable on every machine - measured here failing
        in bursts of tens of seconds while succeeding 40/40 moments later, which
        killed an otherwise-successful session. So the budget is a duration, not
        an attempt count. Only transport errors are retried; an HTTP status is a
        real answer and is raised straight away.
        """
        last: Exception | None = None
        delay = 1.0
        deadline = time.monotonic() + self.connect_budget_seconds
        while True:
            try:
                response = httpx.request(method, url, headers=self._headers(), timeout=30.0, **kwargs)
                response.raise_for_status()
                return response
            except httpx.TransportError as exc:  # DNS / connection, worth retrying
                last = exc
                if time.monotonic() + delay >= deadline:
                    break
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
        raise DevinError(f"{method} {url} failed to connect: {last}")

    def org_id(self) -> str:
        """Configured, or discovered once from /v3/self so nobody has to look it up."""
        if not self._org_id:
            payload = self._request("GET", f"{self.base_url}/self").json()
            self._org_id = payload.get("org_id")
            if not self._org_id:
                raise DevinError(f"/self did not report an org_id: {payload}")
            logger.info("discovered Devin org %s", self._org_id)
        return self._org_id

    def _sessions_url(self) -> str:
        return f"{self.base_url}/organizations/{self.org_id()}/sessions"

    def create_session(self, prompt: str, title: str, schema: dict) -> dict:
        return self._request(
            "POST",
            self._sessions_url(),
            json={
                "prompt": prompt,
                "title": title,
                "structured_output_schema": schema,
                # Make the answer a contract rather than a hope.
                "structured_output_required": True,
                # An ACU is roughly fifteen minutes of work; one question needs
                # a fraction of that, and the ceiling stops a runaway session.
                "max_acu_limit": settings.devin_max_acu,
                "devin_mode": settings.devin_mode,
                "tags": ["kicad-mitos", "clarification"],
            },
        ).json()

    def session(self, session_id: str) -> dict:
        return self._request("GET", f"{self._sessions_url()}/{session_id}").json()

    def run(self, prompt: str, title: str, schema: dict = ANSWER_SCHEMA) -> tuple[dict, str]:
        """Create a session, wait for it to finish, return (structured_output, url).

        Raises DevinError on a terminal failure or when the wait runs out. Callers
        treat that as "unresolved" and fall back to asking the user.
        """
        if not self.available:
            raise DevinError("no Devin API key configured")
        created = self.create_session(prompt, title, schema)
        session_id = created.get("session_id")
        url = created.get("url", "")
        if not session_id:
            raise DevinError(f"Devin did not return a session_id: {created}")

        deadline = time.monotonic() + self.timeout_seconds
        status = "new"
        while True:
            try:
                detail = self.session(session_id)
            except DevinError as exc:
                # A transient poll failure must not throw away a session that is
                # about to answer. Keep polling until the deadline instead.
                logger.warning("poll of %s failed, retrying: %s", session_id, exc)
                if time.monotonic() >= deadline:
                    raise
                time.sleep(self.poll_seconds)
                continue

            status = detail.get("status", "unknown")
            # Read the answer the moment it appears. Measured against the real
            # API, `structured_output` is populated while the session is still
            # `running` - waiting for `exit` costs minutes for no extra
            # information, and is what made this look like a timeout.
            output = detail.get("structured_output")
            if isinstance(output, dict) and output:
                logger.info(
                    "Devin session %s answered while %s, %.2f ACU consumed",
                    session_id,
                    status,
                    detail.get("acus_consumed", 0.0) or 0.0,
                )
                return output, url
            if status in {"exit", "error", "suspended"}:
                raise DevinError(f"session {session_id} ended as {status} with no structured output")
            if time.monotonic() >= deadline:
                raise DevinError(
                    f"session {session_id} still {status} after {self.timeout_seconds:.0f}s"
                )
            time.sleep(self.poll_seconds)


def _peripheral_context(state: ProjectState, selected: list[str]) -> str:
    """Part numbers and pin tables, so the agent can look the real datasheet up.

    This is the thing a plain chat completion cannot do: Devin can read the part's
    documentation rather than pattern-matching five pin names blind.
    """
    lines: list[str] = []
    for reference in selected:
        component = state.components.get(reference)
        if component is None:
            continue
        pins = ", ".join(f"{p.number}:{p.name} ({p.electrical_type})" for p in component.pins)
        lines.append(f"- {reference}: value={component.value!r} lib_id={component.lib_id!r}; pins: {pins}")
    return "\n".join(lines)


def build_prompt(
    state: ProjectState, selected: list[str], instruction: str, clarification: Clarification
) -> str:
    options = "\n".join(f"  - {option}" for option in clarification.options)
    return f"""You are resolving one ambiguity in a KiCAD schematic edit. Answer it and stop.

The engineer asked for:
{instruction.strip() or "(no free-text instruction; the request came from a form)"}

Selected components, with their real pins read from the schematic:
{_peripheral_context(state, selected)}

Nets already present in the project: {", ".join(sorted(state.nets)) or "(none)"}

The question is:
{clarification.question}
(reason code: {clarification.reason})

You must choose exactly one of these options, copied verbatim:
{options}

Look up the part by its value and lib_id if that helps decide — the datasheet is
the authority on which pin carries which signal. Do not modify any files, do not
open a pull request, and do not answer anything other than the question above.
Return the structured output: `answer` must be one of the options exactly as
written, and `reasoning` one sentence saying why."""


def resolve_clarification(
    state: ProjectState,
    selected: list[str],
    instruction: str,
    clarification: Clarification,
    client: DevinClient | None = None,
) -> AgentAnswer | None:
    """Ask the agent this one question. None means "unresolved, go ask the user"."""
    if clarification.reason not in RESOLVABLE_REASONS:
        logger.info("not auto-resolving %s: it is a refusal, not an ambiguity", clarification.reason)
        return None
    if not clarification.options or not clarification.answer_key:
        return None

    client = client or DevinClient()
    try:
        output, url = client.run(
            build_prompt(state, selected, instruction, clarification),
            title=f"KiCAD Mitos: {clarification.reason}",
        )
    except (DevinError, httpx.HTTPError, ValueError) as exc:
        logger.warning("Devin could not resolve %s (%s); asking the user", clarification.reason, exc)
        return None

    answer = str(output.get("answer", "")).strip()
    # Strictly one of the planner's own options. An answer off that list is a
    # hallucinated pin, and the planner would reject it anyway - better to hand
    # the question back than to feed it a value it never offered.
    if answer not in clarification.options:
        logger.warning("Devin answered %r, which is not one of %s", answer, clarification.options)
        return None
    return AgentAnswer(answer=answer, reasoning=str(output.get("reasoning", "")).strip(), session_url=url)


def apply_answer(answers: PlanAnswers, answer_key: str, option: str) -> PlanAnswers:
    """Write one answer into PlanAnswers, honouring the namespaced signal keys."""
    data = answers.model_dump()
    separator = answer_key.find(":")
    if separator == -1:
        data[answer_key] = option
        return PlanAnswers(**data)
    field = "controller_pins" if answer_key[:separator] == "controller_pin" else "peripheral_pins"
    data[field] = {**(data.get(field) or {}), answer_key[separator + 1 :]: option}
    return PlanAnswers(**data)


def resolve_and_plan(
    state: ProjectState,
    selected: list[str],
    instruction: str,
    answers: PlanAnswers,
    clarification: Clarification,
    plan_fn,
    client: DevinClient | None = None,
) -> tuple[ActionPlan | Clarification, PlanAnswers, list[str]]:
    """Resolve ambiguities with the agent until a plan forms or one cannot be.

    Returns (result, answers_used, notes). `notes` records each resolution so the
    plan can show what the agent decided and why, rather than presenting an
    agent's guess as if the schematic had said so.
    """
    client = client or DevinClient()
    notes: list[str] = []
    current: ActionPlan | Clarification = clarification

    for _ in range(MAX_ROUNDS):
        if not isinstance(current, Clarification):
            break
        resolved = resolve_clarification(state, selected, instruction, current, client)
        if resolved is None:
            break
        answers = apply_answer(answers, current.answer_key or "", resolved.answer)
        notes.append(
            f"{resolved.answer} chosen for \"{current.question}\" by the Devin agent"
            + (f": {resolved.reasoning}" if resolved.reasoning else "")
        )
        current = plan_fn(answers)

    if isinstance(current, ActionPlan) and notes:
        current = current.model_copy(update={"assumptions": [*current.assumptions, *notes]})
    return current, answers, notes


def summarize_schema() -> str:
    """The structured-output contract, for docs and the probe."""
    return json.dumps(ANSWER_SCHEMA, indent=2)
