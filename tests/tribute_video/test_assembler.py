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


def test_user_message_adds_gender_attribute_for_known_gender():
    msg = assembler._user_message(
        subject_name="Meera", relationship="friend", subject_gender="she",
        gt_context="", candidates=_CANDS, message_text="", archetype_leads=[],
        edit_instructions=[])
    assert '<subject relationship="friend" gender="a woman">Meera</subject>' in msg


def test_user_message_omits_gender_attribute_when_unknown():
    msg = assembler._user_message(
        subject_name="Meera", relationship="friend", subject_gender=None,
        gt_context="", candidates=_CANDS, message_text="", archetype_leads=[],
        edit_instructions=[])
    assert "gender=" not in msg
    assert '<subject relationship="friend">Meera</subject>' in msg


def test_user_message_omits_gender_attribute_for_neutral_they():
    msg = assembler._user_message(
        subject_name="Meera", relationship=None, subject_gender="they",
        gt_context="", candidates=_CANDS, message_text="", archetype_leads=[],
        edit_instructions=[])
    assert "gender=" not in msg


async def test_neutral_relationship_fallback_no_grandfather(monkeypatch):
    """assemble with relationship=None must NOT inject 'grandfather' into the
    system prompt (CLAUDE.md: no forced-male default)."""
    seen = {}

    async def fake(**kw):
        seen["system_prompt"] = kw["system_prompt"]
        return {
            "cover_title": "T",
            "opener": {"line": "This is the story of Meera.",
                       "art_direction": "a"},
            "beats": [{"moment_id": "m1", "line": "A memory of Meera.",
                       "art_direction": "b"}],
            "closing": {"line": "Thank you, Meera.", "art_direction": "c"},
        }

    monkeypatch.setattr(assembler, "call_with_tool", fake)
    await assembler.assemble_storybook_video(
        settings=_Settings(), subject_name="Meera", relationship=None,
        gt_context="", candidates=_CANDS, message_text="", n_pages=15)

    assert "grandfather" not in seen["system_prompt"].lower()
    # The {relationship} placeholder (inside the default opener slot's "Meet
    # my {relationship}, ..." text) is neutralized, not defaulted to a
    # gendered relation.
    assert "meet my the subject" in seen["system_prompt"].lower()


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
