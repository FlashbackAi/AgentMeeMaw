"""Pure-unit tests for storybook compose (no DB, no LLM).

With ``settings=None`` the tribute assembler falls back to a chronological,
title-captioned script, so we can assert the storybook-specific shaping:
no contributor message, closing line promoted to the final card.
"""

from __future__ import annotations

from flashback.artifacts.presets import resolve_preset
from flashback.storybook.generation import assemble_storybook
from flashback.tribute.theme import STORYBOOK_MAX_PAGES

_PRESET = resolve_preset(None)  # the registry default slug


def _candidates(n: int) -> list[dict]:
    return [
        {
            "id": f"m{i}",
            "title": f"Memory {i}",
            "narrative": f"Narrative {i}",
            "generation_prompt": f"prompt {i}",
            "sensory_details": "warm light" if i % 2 == 0 else None,
        }
        for i in range(n)
    ]


async def test_storybook_compose_no_message_and_promotes_closing() -> None:
    title, script_json, context = await assemble_storybook(
        settings=None,
        candidates=_candidates(5),
        person_name="Dad",
        person_relationship="father",
        preset=_PRESET,
        ground_truth_context=None,
    )

    # No contributor message: fallback closing is promoted to the final card,
    # and the muted closing line is cleared.
    assert context["message_page"]["text"] == "The story of Dad"
    assert context["closing_caption"] == ""

    # Title derives from opening caption (empty on fallback) -> name default.
    assert title == "Dad's Story"

    # Pages are capped at the content budget and carry prompt + caption.
    assert len(context["pages"]) == min(5, STORYBOOK_MAX_PAGES - 1)
    for page in context["pages"]:
        assert page["prompt"]
        assert "caption" in page
        assert page["negative"]

    # composed_at is present for the artifact-job stale-check.
    assert context["composed_at"]
    assert len(script_json["scenes"]) == min(5, STORYBOOK_MAX_PAGES - 1)


async def test_storybook_compose_handles_few_candidates() -> None:
    _title, script_json, context = await assemble_storybook(
        settings=None,
        candidates=_candidates(2),
        person_name="Mum",
        person_relationship=None,
        preset=_PRESET,
        ground_truth_context=None,
    )
    assert len(context["pages"]) == 2
    assert len(script_json["scenes"]) == 2
