from __future__ import annotations

from typing import get_args

from flashback.intent_classifier.schema import Intent
from flashback.response_generator import prompts


def test_prompt_constants_are_non_empty():
    for name in [
        "BASE_SYSTEM_PROMPT",
        "CLARIFY_PROMPT",
        "RECALL_PROMPT",
        "DEEPEN_PROMPT",
        "STORY_PROMPT",
        "SWITCH_PROMPT",
        "STARTER_OPENER_PROMPT",
    ]:
        assert getattr(prompts, name).strip()


def test_intent_to_prompt_covers_all_intents():
    assert set(prompts.INTENT_TO_PROMPT) == set(get_args(Intent))


def test_starter_opener_prompt_contains_guardrails():
    prompt = prompts.STARTER_OPENER_PROMPT

    assert "Flashback" in prompt
    assert "Do NOT ask the contributor how they are" in prompt
    assert "Do NOT mention death, dying, passing, loss, or grief" in prompt
    assert "Do NOT mention \"I'm sorry for your loss.\"" in prompt


def test_base_system_prompt_is_in_every_intent_prompt():
    for prompt in prompts.INTENT_TO_PROMPT.values():
        assert prompts.BASE_SYSTEM_PROMPT in prompt
    assert prompts.BASE_SYSTEM_PROMPT in prompts.STARTER_OPENER_PROMPT


def test_no_contributor_display_name_in_chat_prompts():
    """The contributor's display name is archive-side only.
    Response generator and opener prompts must stay neutral —
    no `<contributor_display_name>` tag, no instructions to address
    the contributor by name. The chat surface stays neutral by design.
    """
    chat_prompts = list(prompts.INTENT_TO_PROMPT.values()) + [
        prompts.STARTER_OPENER_PROMPT
    ]
    for p in chat_prompts:
        assert "<contributor_display_name>" not in p
        assert "contributor's display name" not in p.lower()
