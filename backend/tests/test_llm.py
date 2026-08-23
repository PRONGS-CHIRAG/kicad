from __future__ import annotations

from app.planning.llm import SYSTEM_PROMPT


def test_system_prompt_advertises_place_footprint() -> None:
    """Regression guard: an LLM plan missing this would validate cleanly while quietly dropping placement."""
    assert "place_footprint" in SYSTEM_PROMPT
