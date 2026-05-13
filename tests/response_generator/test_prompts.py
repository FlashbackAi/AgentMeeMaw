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
        "FIRST_TIME_OPENER_PROMPT",
    ]:
        assert getattr(prompts, name).strip()


def test_intent_to_prompt_covers_all_intents():
    assert set(prompts.INTENT_TO_PROMPT) == set(get_args(Intent))


def test_starter_opener_prompt_contains_guardrails():
    prompt = prompts.STARTER_OPENER_PROMPT

    assert "Flashback" in prompt
    assert "Do NOT ask the contributor how they are" in prompt
    assert "Do NOT use condolence formulas" in prompt
    assert "Do NOT mention the subject's life status" in prompt


def test_starter_opener_prompt_does_not_render_archetype_answers():
    """Archetype answers belong to the first-time opener path only.
    They have already been absorbed into the graph by session 2 — the
    normal opener prompt must not anchor on them, instruct the LLM to
    use them, or expect an ``<archetype_answers>`` block to be rendered.
    A negative reference ("don't re-ask onboarding shapes") is fine."""

    prompt = prompts.STARTER_OPENER_PROMPT

    assert "<archetype_answers>" not in prompt
    assert "Use the most concrete thing" not in prompt


def test_first_time_opener_prompt_anchors_on_archetype_answers():
    prompt = prompts.FIRST_TIME_OPENER_PROMPT
    flattened = " ".join(prompt.split())

    assert "Flashback" in prompt
    assert "<archetype_answers>" in prompt
    assert "very first message" in flattened
    assert "NEVER re-ask anything the archetype answers already captured" in prompt
    assert "Do NOT enumerate the archetype answers" in prompt
    assert "Do NOT use condolence formulas" in prompt
    assert "Do NOT mention the subject's life status" in prompt


def test_base_system_prompt_is_in_every_intent_prompt():
    for prompt in prompts.INTENT_TO_PROMPT.values():
        assert prompts.BASE_SYSTEM_PROMPT in prompt
    assert prompts.BASE_SYSTEM_PROMPT in prompts.STARTER_OPENER_PROMPT
    assert prompts.BASE_SYSTEM_PROMPT in prompts.FIRST_TIME_OPENER_PROMPT


def test_no_contributor_display_name_in_chat_prompts():
    """The contributor's display name is archive-side only.
    Response generator and opener prompts must stay neutral —
    no `<contributor_display_name>` tag, no instructions to address
    the contributor by name. The chat surface stays neutral by design.
    """
    chat_prompts = list(prompts.INTENT_TO_PROMPT.values()) + [
        prompts.STARTER_OPENER_PROMPT,
        prompts.FIRST_TIME_OPENER_PROMPT,
    ]
    for p in chat_prompts:
        assert "<contributor_display_name>" not in p
        assert "contributor's display name" not in p.lower()


def test_prompts_are_status_neutral_by_default():
    prompt = prompts.BASE_SYSTEM_PROMPT

    assert "living, deceased, or known through inherited family stories" in prompt
    assert "Never infer the subject's life status" in prompt
    assert "Mirror the contributor's" in prompt


def test_contributor_name_is_private_opener_context_only():
    prompt = prompts.STARTER_OPENER_PROMPT
    flattened = " ".join(prompt.split())

    assert "<contributor_name>" in prompt
    assert "private context" in flattened
    assert "Do NOT use the contributor's own name as a greeting or address" in prompt
