"""Assembler voice/opener/art slots + profile-aware fallback templates.

The system prompt gains three injection slots filled from CRM config
(composed by flashback.tribute.composer). All-None slots must reproduce the
shipped register so legacy snapshots render identically.
"""
from __future__ import annotations

from dataclasses import dataclass

from flashback.tribute_video import assembler


@dataclass
class _Settings:
    llm_big_provider: str = "anthropic"
    llm_big_model: str = "claude-sonnet-4-6"


_CANDS = [
    {"id": "m1", "title": "The chai stall", "narrative": "Every exam eve."},
    {"id": "m2", "title": "The rescue", "narrative": "Showed up at 2am."},
]

_OK_TOOL_OUTPUT = {
    "cover_title": "Partner in Crime",
    "opener": {"line": "Nobody warned anyone about Arjun.", "art_direction": "x"},
    "beats": [
        {"moment_id": "m1", "line": "Every exam eve ended at the chai stall.",
         "art_direction": "y"},
    ],
    "closing": {"line": "Some friends are simply family.", "art_direction": "z"},
}


async def test_custom_slots_injected_and_guardrails_kept(monkeypatch):
    captured: dict = {}

    async def fake(**kw):
        captured.update(kw)
        return _OK_TOOL_OUTPUT

    monkeypatch.setattr(assembler, "call_with_tool", fake)
    await assembler.assemble_storybook_video(
        settings=_Settings(), subject_name="Arjun", relationship="best friend",
        gt_context="", candidates=_CANDS, n_pages=8,
        voice_block="VOICEX partner-in-crime energy.",
        opener_style="OPENX like a story told at every party.",
        art_mood="ARTX bright, candid, mid-motion.",
    )
    system = captured["system_prompt"]
    assert "VOICEX" in system
    assert "OPENX" in system
    assert "ARTX" in system
    # guardrails survive custom slots
    assert "8 to 10 words" in system
    assert "NEVER a face" in system
    assert "compose_book" in system
    # the default family register is fully replaced
    assert "a child, a grandchild" not in system
    assert 'Meet my {relationship}' not in system


async def test_none_slots_reproduce_shipped_register(monkeypatch):
    captured: dict = {}

    async def fake(**kw):
        captured.update(kw)
        return _OK_TOOL_OUTPUT

    monkeypatch.setattr(assembler, "call_with_tool", fake)
    await assembler.assemble_storybook_video(
        settings=_Settings(), subject_name="Chandraiah",
        relationship="grandfather", gt_context="", candidates=_CANDS, n_pages=8,
    )
    system = captured["system_prompt"]
    assert "a loved one speaking, warm and proud" in system
    # With no authored narrative, the memorial default FRAMING reproduces the
    # original hard-coded life-story framing.
    assert "a reader who never met them" in system
    assert "one connected life arc" in system
    assert "Meet my grandfather" in system  # {relationship} substituted
    assert "8 to 10 words" in system
    assert "NEVER a face" in system


async def test_custom_narrative_replaces_memorial_framing(monkeypatch):
    captured: dict = {}

    async def fake(**kw):
        captured.update(kw)
        return _OK_TOOL_OUTPUT

    monkeypatch.setattr(assembler, "call_with_tool", fake)
    await assembler.assemble_storybook_video(
        settings=_Settings(), subject_name="Arjun", relationship="friend",
        gt_context="", candidates=_CANDS, n_pages=8,
        narrative_block=(
            "FRAMING -- who this is for and how it's shaped:\n"
            "- AUDIENCE: the friend themselves and the circle who knows you both\n"
            "- ARC: how you met, the everyday, drifting, the reunion\n"
            "- THROUGHLINE: the shared jokes and small loyalties"),
    )
    system = captured["system_prompt"]
    # the authored friendship framing is present...
    assert "the friend themselves and the circle" in system
    assert "the everyday, drifting, the reunion" in system
    # ...and the memorial default is fully gone
    assert "a reader who never met them" not in system
    assert "one connected life arc" not in system
    # the arc/closing instructions defer to FRAMING, not a hard-coded life arc
    assert "along the ARC described in FRAMING" in system
    assert "lands the THROUGHLINE" in system


async def test_name_placeholder_substituted_in_custom_opener(monkeypatch):
    captured: dict = {}

    async def fake(**kw):
        captured.update(kw)
        return _OK_TOOL_OUTPUT

    monkeypatch.setattr(assembler, "call_with_tool", fake)
    await assembler.assemble_storybook_video(
        settings=_Settings(), subject_name="Arjun", relationship="best friend",
        gt_context="", candidates=_CANDS, n_pages=8,
        opener_style="Open with a tease: 'Nobody warned me about {name}.'",
    )
    assert "Nobody warned me about Arjun." in captured["system_prompt"]


async def test_fallback_uses_profile_templates() -> None:
    book = await assembler.assemble_storybook_video(
        settings=None, subject_name="Arjun", relationship="best friend",
        gt_context="", candidates=_CANDS, n_pages=8,
        fallback_opener="Nobody warned me about {name}.",
        fallback_closing="For every laugh - thank you, {name}.",
    )
    assert book.opener.line == "Nobody warned me about Arjun."
    assert book.closing.line == "For every laugh - thank you, Arjun."


async def test_fallback_without_templates_keeps_current_lines() -> None:
    book = await assembler.assemble_storybook_video(
        settings=None, subject_name="Arjun", relationship="best friend",
        gt_context="", candidates=_CANDS, n_pages=8,
    )
    assert book.opener.line == "Meet my best friend, Arjun."
    assert book.closing.line == "Thank you for everything, Arjun."


async def test_fallback_template_never_raises_on_unknown_key() -> None:
    book = await assembler.assemble_storybook_video(
        settings=None, subject_name="Arjun", relationship=None,
        gt_context="", candidates=_CANDS, n_pages=8,
        fallback_opener="Hey {nickname}, it's {name}.",
    )
    # unknown key collapses to empty instead of raising
    assert book.opener.line == "Hey , it's Arjun."
