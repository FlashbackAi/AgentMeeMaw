from __future__ import annotations

from flashback.profile_facts.prompts import SYSTEM_PROMPT


def test_prompt_preserves_actor_attribution() -> None:
    assert "Preserve actor attribution" in SYSTEM_PROMPT
    assert "clearly says the fact is about the legacy subject" in SYSTEM_PROMPT
    assert "OMIT the fact" in SYSTEM_PROMPT
