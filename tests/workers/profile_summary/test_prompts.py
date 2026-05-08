"""Drift detector for the Profile Summary system prompt.

The negative tone constraints (no platitudes) are behaviorally
important. If someone removes them, these assertions catch it.
"""

from __future__ import annotations

from flashback.workers.profile_summary.prompts import SYSTEM_PROMPT


def test_system_prompt_non_empty() -> None:
    assert SYSTEM_PROMPT.strip()


def test_system_prompt_names_role() -> None:
    """The model needs to know what it is."""
    assert "Profile Summary Generator" in SYSTEM_PROMPT
    assert "Flashback" in SYSTEM_PROMPT


def test_system_prompt_forbids_known_platitudes() -> None:
    """Drift detector — the negative-constraint clauses must be present.

    These three are the explicit ones called out in the spec. If a
    future edit drops them, the model will start producing platitude-
    laden summaries and there's no other safety net.
    """
    assert "rest in peace" in SYSTEM_PROMPT
    assert "will be missed" in SYSTEM_PROMPT
    assert "thoughts and prayers" in SYSTEM_PROMPT


def test_system_prompt_says_word_count() -> None:
    """Length budget is part of the contract — keep it visible."""
    assert "150-300 words" in SYSTEM_PROMPT


def test_system_prompt_forbids_impersonation() -> None:
    """Aligns with the same constraint in response_generator/prompts.py."""
    assert "Never speak as if you are the deceased" in SYSTEM_PROMPT


def test_system_prompt_preserves_actor_attribution() -> None:
    """Summaries must not swap who did what when several people appear."""
    assert "Preserve actor attribution" in SYSTEM_PROMPT
    assert "Never shift an action" in SYSTEM_PROMPT


def test_system_prompt_mentions_contributor_display_name() -> None:
    """Archive-side prompt should describe how to use the contributor's
    display name for natural attribution and how to fall back when empty."""
    assert "<contributor_display_name>" in SYSTEM_PROMPT
    assert "neutral attribution" in SYSTEM_PROMPT
