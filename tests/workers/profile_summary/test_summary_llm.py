"""Tests for the summary LLM wrapper (mocked transport)."""

from __future__ import annotations

import pytest

from flashback.llm.errors import LLMMalformedResponse, LLMTimeout
from flashback.workers.profile_summary import summary_llm as summary_mod
from flashback.workers.profile_summary.schema import (
    ProfileSummaryContext,
    TimePeriodView,
    TraitView,
)
from flashback.workers.profile_summary.summary_llm import generate_summary

from tests.workers.profile_summary.conftest import (
    failing_call_text,
    queued_call_text,
)


def _ctx() -> ProfileSummaryContext:
    return ProfileSummaryContext(
        person_id="00000000-0000-0000-0000-000000000000",
        person_name="Margaret",
        relationship="mother",
        traits=[
            TraitView(name="Patient", description="Took her time.", strength="strong")
        ],
        threads=[],
        entities=[],
        time_period=TimePeriodView(year_range=None, life_periods=[]),
    )


def test_generate_summary_happy_path(monkeypatch, stub_summary_cfg, stub_settings):
    """Mock returns prose; generate strips and returns it."""
    monkeypatch.setattr(
        summary_mod,
        "call_text",
        queued_call_text(["  A short summary about Margaret.  \n"]),
    )
    text = generate_summary(
        cfg=stub_summary_cfg, settings=stub_settings, context=_ctx()
    )
    assert text == "A short summary about Margaret."


def test_generate_summary_empty_string_is_malformed(
    monkeypatch, stub_summary_cfg, stub_settings
):
    monkeypatch.setattr(
        summary_mod, "call_text", queued_call_text([""])
    )
    with pytest.raises(LLMMalformedResponse):
        generate_summary(
            cfg=stub_summary_cfg, settings=stub_settings, context=_ctx()
        )


def test_generate_summary_whitespace_only_is_malformed(
    monkeypatch, stub_summary_cfg, stub_settings
):
    monkeypatch.setattr(
        summary_mod, "call_text", queued_call_text(["   \n   \t  "])
    )
    with pytest.raises(LLMMalformedResponse):
        generate_summary(
            cfg=stub_summary_cfg, settings=stub_settings, context=_ctx()
        )


def test_generate_summary_propagates_timeout(
    monkeypatch, stub_summary_cfg, stub_settings
):
    monkeypatch.setattr(
        summary_mod, "call_text", failing_call_text(LLMTimeout("slow"))
    )
    with pytest.raises(LLMTimeout):
        generate_summary(
            cfg=stub_summary_cfg, settings=stub_settings, context=_ctx()
        )
