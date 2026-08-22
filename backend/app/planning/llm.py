"""Optional LLM instruction-to-plan conversion.

The LLM may only produce the same strict schema the deterministic planner uses,
and never executes anything itself. If no API key is configured, or the model
returns something that does not validate, the deterministic planner is used.
"""

from __future__ import annotations

import json
import logging

import httpx
from pydantic import ValidationError

from ..config import settings
from ..kicad.reader import ProjectState
from ..models import ActionPlan, Clarification, PlanAnswers
from .generator import generate_plan
from .instruction import parse_instruction
from .validator import validate_plan

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You convert an electronics engineer's instruction into a strict JSON action plan
for a KiCAD schematic. Only the I2C protocol is supported. Use only components, pins and nets that
exist in the provided project state. Never invent pins. If the request is ambiguous, unsafe, or the
schematic does not identify the needed pins, return a clarification instead of a plan.

Return JSON with either:
{"plan": {...ActionPlan...}} or {"clarification": {"question": str, "reason": str, "options": [str]}}

ActionPlan fields: schema_version, goal, selected_components, protocol, logic_voltage, assumptions,
warnings, protected_objects, actions. Allowed action types:
  {"id","type":"connect_pins","from":"U2.SDA","to":"U1.GPIO21","net_name":"I2C_SDA","purpose":""}
  {"id","type":"connect_pin_to_net","pin":"U2.VCC","net":"+3V3","purpose":""}
  {"id","type":"ensure_pullup","net":"I2C_SDA","to_net":"+3V3","value":"4.7k","purpose":""}
"""


class PlanGenerationError(RuntimeError):
    pass


def _context(state: ProjectState, selected: list[str]) -> dict:
    return {
        "selected_components": selected,
        "components": state.component_summaries(),
        "nets": {name: pins for name, pins in sorted(state.nets.items())},
    }


def _call_openai(payload: dict) -> dict:
    response = httpx.post(
        f"{settings.openai_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": settings.llm_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload)},
            ],
        },
        timeout=settings.llm_timeout_seconds,
    )
    response.raise_for_status()
    return json.loads(response.json()["choices"][0]["message"]["content"])


def plan_from_instruction(
    state: ProjectState,
    selected: list[str],
    instruction: str,
    answers: PlanAnswers | None = None,
) -> tuple[ActionPlan | Clarification, str]:
    """Return (plan_or_clarification, source) where source is 'llm' or 'rules'."""
    fallback = generate_plan(state, selected, instruction, parse_instruction(instruction), answers)
    # `exclude_defaults` (not just `exclude_none`) because PlanAnswers now carries
    # dict fields whose default is `{}`, which is not None: without it every API
    # request, which always sends an empty PlanAnswers, would look "answered".
    if not settings.llm_enabled or (answers and answers.model_dump(exclude_defaults=True)):
        return fallback, "rules"

    try:
        raw = _call_openai({"instruction": instruction, "project": _context(state, selected)})
        if "clarification" in raw:
            return Clarification.model_validate(raw["clarification"]), "llm"
        plan = ActionPlan.model_validate(raw["plan"])
        problems = validate_plan(state, plan)
        if problems:
            logger.warning("LLM plan rejected by validator: %s", problems)
            return fallback, "rules"
        return plan, "llm"
    except (httpx.HTTPError, KeyError, ValueError, ValidationError) as exc:
        logger.warning("LLM planning failed (%s); using deterministic planner", exc)
        return fallback, "rules"
