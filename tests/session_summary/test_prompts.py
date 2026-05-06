from __future__ import annotations

import re

from flashback.session_summary.prompts import SYSTEM_PROMPT


def test_prompt_contains_next_session_framing():
    assert "Last time, you talked about" in SYSTEM_PROMPT
    assert "natural continuation" in SYSTEM_PROMPT


def test_prompt_does_not_include_forbidden_platitude_examples():
    assert not re.search(r"\bmeaningful\b", SYSTEM_PROMPT, re.IGNORECASE)
    assert not re.search(r"beautiful memories", SYSTEM_PROMPT, re.IGNORECASE)
    assert not re.search(r"It was a pleasure", SYSTEM_PROMPT, re.IGNORECASE)


def test_prompt_preserves_actor_attribution():
    assert "Preserve actor attribution" in SYSTEM_PROMPT
    assert "Never change an action's actor" in SYSTEM_PROMPT
