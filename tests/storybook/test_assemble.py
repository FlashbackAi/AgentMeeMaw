"""Pure-unit tests for storybook shaping (no DB, no LLM).

Covers the no-cover storybook context, the script JSON round-trip, and the
emotional-tag registry.
"""

from __future__ import annotations

from flashback.artifacts.presets import resolve_preset
from flashback.storybook.generation import (
    _finalize_book_script,
    _script_from_json,
    _script_to_json,
)
from flashback.storybook.tags import (
    MAX_STORYBOOK_TAGS,
    normalize_tags,
    render_tag_catalog,
)
from flashback.tribute.artifact_context import build_storybook_context
from flashback.tribute.assembly import Scene, TributeScript
from flashback.tribute.theme import STORYBOOK_MAX_PAGES

_PRESET = resolve_preset(None)


def _script(n: int) -> TributeScript:
    return TributeScript(
        scenes=[
            Scene(
                moment_id=f"m{i}",
                caption=f"Caption {i}",
                accent=f"accent {i}",
                art_direction=f"art {i}",
            )
            for i in range(n)
        ],
        opening_caption="We open here.",
        closing_caption="And we close here.",
        message_text="",
        cover_title="A Quiet Life",
        tags=("warmth", "nostalgia"),
    )


def _moments(n: int) -> dict[str, dict]:
    return {
        f"m{i}": {"id": f"m{i}", "generation_prompt": f"prompt {i}", "time_anchor": None}
        for i in range(n)
    }


def test_storybook_context_has_no_cover_and_promotes_closing() -> None:
    book = _finalize_book_script(_script(5), "Dad")
    context = build_storybook_context(
        script=book,
        moments_by_id=_moments(5),
        preset=_PRESET,
        max_pages=STORYBOOK_MAX_PAGES,
        include_cover=False,
    )

    # No cover page on a standalone storybook.
    assert "cover" not in context
    # The closing line is promoted to the final card.
    assert context["message_page"]["text"] == "And we close here."
    assert context["closing_caption"] == ""
    # Without a cover, the full page budget is available for content.
    assert len(context["pages"]) == min(5, STORYBOOK_MAX_PAGES)
    for page in context["pages"]:
        assert page["prompt"]
        assert page["negative"]
    assert context["composed_at"]


def test_closing_fallback_uses_person_name() -> None:
    book = _finalize_book_script(
        TributeScript(scenes=[], opening_caption="", closing_caption="", message_text=""),
        "Mum",
    )
    assert book.message_text == "The story of Mum"


def test_script_json_round_trip_preserves_art_direction_and_tags() -> None:
    book = _finalize_book_script(_script(3), "Dad")
    data = _script_to_json(book)
    restored = _script_from_json(data)

    assert [s.moment_id for s in restored.scenes] == [s.moment_id for s in book.scenes]
    assert [s.art_direction for s in restored.scenes] == ["art 0", "art 1", "art 2"]
    assert restored.message_text == book.message_text
    assert restored.tags == ("warmth", "nostalgia")


def test_normalize_tags_validates_dedupes_and_caps() -> None:
    out = normalize_tags(["Happiness", "happiness", "not_a_tag", "GRIEF", "love", "pride"])
    # de-duped, lower-cased, unknown dropped, capped.
    assert out == ["happiness", "grief", "love"][:MAX_STORYBOOK_TAGS]
    assert normalize_tags(None) == []
    assert normalize_tags([]) == []


def test_render_tag_catalog_lists_registry_slugs() -> None:
    catalog = render_tag_catalog()
    assert "<emotional_tags>" in catalog
    assert 'slug="happiness"' in catalog
    assert 'slug="grief"' in catalog
