"""Extraction LLM wrapper tests (no DB, no network)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from flashback.llm.errors import LLMTimeout
from flashback.workers.extraction import extraction_llm as ext_llm_mod
from flashback.workers.extraction.extraction_llm import (
    ExtractionLLMConfig,
    run_extraction,
)
from flashback.workers.extraction.schema import SegmentTurn
from tests.workers.extraction.fixtures import sample_extractions


SEGMENT_TURNS = [
    SegmentTurn(
        role="assistant",
        content="Tell me about him.",
        timestamp="2026-05-04T12:00:00+00:00",
    ),
    SegmentTurn(
        role="user",
        content="He was warm.",
        timestamp="2026-05-04T12:00:30+00:00",
    ),
]


def _stub_call(returns: dict, exception: Exception | None = None):
    async def _impl(**kwargs):
        if exception is not None:
            raise exception
        return returns

    return _impl


def test_happy_path(monkeypatch, stub_extraction_cfg, stub_settings) -> None:
    payload = sample_extractions.clean_extraction()
    monkeypatch.setattr(
        ext_llm_mod, "call_with_tool", _stub_call(payload)
    )
    result = run_extraction(
        cfg=stub_extraction_cfg,
        settings=stub_settings,
        subject_name="Dad",
        subject_relationship="father",
        prior_rolling_summary="They had spoken about him before.",
        segment_turns=SEGMENT_TURNS,
    )
    assert len(result.moments) == 2
    assert len(result.entities) == 3
    assert result.traits[0].name == "warmth"


def test_empty_extraction_is_valid(
    monkeypatch, stub_extraction_cfg, stub_settings
) -> None:
    payload = sample_extractions.empty_extraction()
    monkeypatch.setattr(ext_llm_mod, "call_with_tool", _stub_call(payload))
    result = run_extraction(
        cfg=stub_extraction_cfg,
        settings=stub_settings,
        subject_name="Dad",
        subject_relationship=None,
        prior_rolling_summary="",
        segment_turns=SEGMENT_TURNS,
    )
    assert result.moments == []
    assert result.entities == []


def test_llm_timeout_propagates(
    monkeypatch, stub_extraction_cfg, stub_settings
) -> None:
    monkeypatch.setattr(
        ext_llm_mod,
        "call_with_tool",
        _stub_call({}, exception=LLMTimeout("slow")),
    )
    with pytest.raises(LLMTimeout):
        run_extraction(
            cfg=stub_extraction_cfg,
            settings=stub_settings,
            subject_name="Dad",
            subject_relationship=None,
            prior_rolling_summary="",
            segment_turns=SEGMENT_TURNS,
        )


def test_contributor_display_name_in_user_message(
    monkeypatch, stub_extraction_cfg, stub_settings
) -> None:
    payload = sample_extractions.empty_extraction()
    captured: dict = {}

    async def _impl(**kwargs):
        captured.update(kwargs)
        return payload

    monkeypatch.setattr(ext_llm_mod, "call_with_tool", _impl)
    run_extraction(
        cfg=stub_extraction_cfg,
        settings=stub_settings,
        subject_name="Dad",
        subject_relationship=None,
        prior_rolling_summary="",
        segment_turns=SEGMENT_TURNS,
        contributor_display_name="Sarah",
    )
    user_message = captured["user_message"]
    assert "<contributor_display_name>Sarah</contributor_display_name>" in user_message


def test_contributor_display_name_empty_renders_empty_tag(
    monkeypatch, stub_extraction_cfg, stub_settings
) -> None:
    payload = sample_extractions.empty_extraction()
    captured: dict = {}

    async def _impl(**kwargs):
        captured.update(kwargs)
        return payload

    monkeypatch.setattr(ext_llm_mod, "call_with_tool", _impl)
    run_extraction(
        cfg=stub_extraction_cfg,
        settings=stub_settings,
        subject_name="Dad",
        subject_relationship=None,
        prior_rolling_summary="",
        segment_turns=SEGMENT_TURNS,
    )
    user_message = captured["user_message"]
    assert "<contributor_display_name></contributor_display_name>" in user_message


def test_validation_error_on_missing_themes(
    monkeypatch, stub_extraction_cfg, stub_settings
) -> None:
    payload = sample_extractions.empty_extraction()
    payload["dropped_references"] = [
        {
            "dropped_phrase": "Aunt Mavis",
            "question_text": "Who was she?",
            "themes": [],
        }
    ]
    monkeypatch.setattr(ext_llm_mod, "call_with_tool", _stub_call(payload))
    with pytest.raises(ValidationError):
        run_extraction(
            cfg=stub_extraction_cfg,
            settings=stub_settings,
            subject_name="Dad",
            subject_relationship=None,
            prior_rolling_summary="",
            segment_turns=SEGMENT_TURNS,
        )
