"""Unit tests for the storybook-video assembler (LLM mocked)."""
from __future__ import annotations

from dataclasses import dataclass

from flashback.tribute_video import assembler
from flashback.tribute_video.book import Book


@dataclass
class _Settings:
    llm_big_provider: str = "anthropic"
    llm_big_model: str = "claude-sonnet-4-6"


_CANDS = [
    {"id": "m1", "title": "In the field", "narrative": "Farmed at ten.",
     "sensory_details": "dust and sorghum"},
    {"id": "m2", "title": "The buffalo", "narrative": "Raised Lakshmi."},
]


async def test_maps_tool_output_and_drops_unknown_moment(monkeypatch):
    async def fake(**kw):
        return {
            "cover_title": "The Man Who Kept Working",
            "opener": {"line": "Meet my grandfather, who fed us all.",
                       "art_direction": "an old farmer at dawn"},
            "beats": [
                {"moment_id": "m1", "line": "He farmed beside his father at ten.",
                 "art_direction": "a boy and his father in a field"},
                {"moment_id": "ghost", "line": "Should be dropped.",
                 "art_direction": "x"},
                {"moment_id": "m2", "line": "He spoke to the buffalo like family.",
                 "art_direction": "a man and a buffalo at a fence"},
            ],
            "closing": {"line": "He left us steadier than he found us.",
                        "art_direction": "an empty chair on a porch at dusk"},
        }

    monkeypatch.setattr(assembler, "call_with_tool", fake)
    book = await assembler.assemble_storybook_video(
        settings=_Settings(), subject_name="Chandraiah",
        relationship="Grand Father", gt_context="rural Telangana",
        candidates=_CANDS, message_text="I love you, thatha.",
        archetype_leads=["farming", "family first"], n_pages=15)

    assert isinstance(book, Book)
    assert book.cover_title == "The Man Who Kept Working"
    assert book.opener.line.startswith("Meet my")
    assert [b.moment_id for b in book.beats] == ["m1", "m2"]  # 'ghost' dropped
    assert book.closing.line
    assert book.message == "I love you, thatha."


async def test_falls_back_on_llm_error(monkeypatch):
    from flashback.llm.errors import LLMError

    async def boom(**kw):
        raise LLMError("upstream down")

    monkeypatch.setattr(assembler, "call_with_tool", boom)
    book = await assembler.assemble_storybook_video(
        settings=_Settings(), subject_name="C", relationship=None,
        gt_context="", candidates=_CANDS, message_text="bye",
        archetype_leads=[], n_pages=15)

    assert isinstance(book, Book)
    assert [b.moment_id for b in book.beats] == ["m1", "m2"]  # title-derived
    assert book.message == "bye"


async def test_empty_candidates_returns_safe_fallback(monkeypatch):
    called = False

    async def fake(**kw):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(assembler, "call_with_tool", fake)
    book = await assembler.assemble_storybook_video(
        settings=_Settings(), subject_name="C", relationship=None,
        gt_context="", candidates=[], message_text="", n_pages=15)

    assert isinstance(book, Book)
    assert called is False  # no LLM call when there are no usable candidates


async def test_fallback_book_always_carries_text(monkeypatch):
    """The degraded book must never ship image-only pages: the opener and
    closing lines + cover title are template-derived, not empty."""
    from flashback.llm.errors import LLMError

    async def boom(**kw):
        raise LLMError("timeout")

    monkeypatch.setattr(assembler, "call_with_tool", boom)
    book = await assembler.assemble_storybook_video(
        settings=_Settings(), subject_name="Chandraiah",
        relationship="Grand Father", gt_context="", candidates=_CANDS,
        message_text="", archetype_leads=[], n_pages=15)

    assert "Chandraiah" in book.opener.line
    assert book.opener.line.startswith("Meet my grand father")
    assert book.closing.line
    assert book.cover_title
    assert all(b.line for b in book.beats)
