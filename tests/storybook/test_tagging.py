"""storybook.tagging: slug validation + batch tagging (LLM mocked)."""

from __future__ import annotations

import pytest

from flashback.storybook import tagging
from flashback.storybook.tagging import _validate, tag_moments


class _Settings:
    llm_big_provider = "anthropic"
    llm_big_model = "claude-sonnet-4-6"


def test_validate_drops_unknown_and_dedupes() -> None:
    assert _validate(["childhood", "memoir", "childhood", "festivals"]) == [
        "childhood",
        "festivals",
    ]
    assert _validate(None) == []
    assert _validate(["", "  ", 7]) == []


async def test_tag_moments_maps_by_index_and_fills_untagged(monkeypatch) -> None:
    async def _fake_call(**_kwargs):
        return {
            "moments": [
                {"index": 0, "collections": ["childhood", "bogus"]},
                {"index": 2, "collections": []},
                {"index": 9, "collections": ["festivals"]},  # out of range
            ]
        }

    monkeypatch.setattr(tagging, "call_with_tool", _fake_call)
    moments = [
        {"id": "a", "title": "t", "narrative": "n"},
        {"id": "b", "title": "t", "narrative": "n"},
        {"id": "c", "title": "t", "narrative": "n"},
    ]
    got = await tag_moments(
        settings=_Settings(), provider="anthropic", model="m",
        subject_name="Dad", relationship="father", moments=moments,
    )
    # bogus slug dropped; unmentioned index -> []; out-of-range ignored.
    assert got == {"a": ["childhood"], "b": [], "c": []}


async def test_tag_moments_empty_input_no_call(monkeypatch) -> None:
    async def _boom(**_kwargs):
        raise AssertionError("must not call the LLM for an empty batch")

    monkeypatch.setattr(tagging, "call_with_tool", _boom)
    assert await tag_moments(
        settings=_Settings(), provider="p", model="m",
        subject_name="Dad", relationship=None, moments=[],
    ) == {}
