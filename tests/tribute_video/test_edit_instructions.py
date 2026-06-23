"""The assembler renders family edit requests; suggestions fall back safely.

No LLM / DB here -- we assert the prompt-building (`_user_message` emits the
<family_edit_requests> block iff instructions are present) and the
best-effort fallback shape of the contextual edit-suggestion chips.
"""
from __future__ import annotations

import asyncio

from flashback.tribute_video.assembler import _user_message
from flashback.tribute_video.edit_suggestions import (
    FALLBACK_SUGGESTIONS,
    generate_edit_suggestions,
)

_CANDIDATES = [{"id": "1", "title": "Lake", "narrative": "Fishing at dawn."}]


def test_user_message_includes_edit_requests_when_present():
    msg = _user_message(
        subject_name="Dad", relationship="father", gt_context="",
        candidates=_CANDIDATES, message_text="", archetype_leads=[],
        edit_instructions=["Make it warmer.", "Lean on the fishing trips."])
    assert "<family_edit_requests>" in msg
    assert "<request>Make it warmer.</request>" in msg
    assert "<request>Lean on the fishing trips.</request>" in msg


def test_user_message_omits_block_when_no_edits():
    msg = _user_message(
        subject_name="Dad", relationship="father", gt_context="",
        candidates=_CANDIDATES, message_text="", archetype_leads=[],
        edit_instructions=[])
    assert "family_edit_requests" not in msg


def test_suggestions_fallback_without_settings():
    out = asyncio.run(generate_edit_suggestions(
        settings=None, subject_name="Dad", relationship="father",
        candidates=_CANDIDATES))
    assert out == FALLBACK_SUGGESTIONS
    assert all({"label", "instruction"} <= set(s) for s in out)


def test_suggestions_fallback_without_candidates():
    # Even with a (truthy) settings object, no usable memories -> fallback,
    # never an LLM call.
    out = asyncio.run(generate_edit_suggestions(
        settings=object(), subject_name="Dad", relationship="father",
        candidates=[]))
    assert out == FALLBACK_SUGGESTIONS
