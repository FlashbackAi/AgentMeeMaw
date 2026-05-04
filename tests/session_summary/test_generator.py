from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flashback.llm.errors import LLMTimeout
from flashback.session_summary import generator as generator_module
from flashback.session_summary.generator import SessionSummaryGenerator
from flashback.session_summary.schema import SessionSummaryContext


SETTINGS = SimpleNamespace(
    llm_session_summary_provider="anthropic",
    llm_session_summary_model="claude-sonnet-4-6",
    llm_session_summary_timeout_seconds=12,
    llm_session_summary_max_tokens=300,
    anthropic_api_key="key",
)


def _ctx(rolling_summary: str = "They talked about the lake cabin."):
    return SessionSummaryContext(
        person_name="Maya",
        relationship="mother",
        rolling_summary=rolling_summary,
    )


async def test_generate_happy_path(monkeypatch):
    call = AsyncMock(return_value="  the lake cabin summers  ")
    monkeypatch.setattr(generator_module, "call_text", call)

    result = await SessionSummaryGenerator(SETTINGS).generate(_ctx())

    assert result.text == "the lake cabin summers"
    assert call.await_args.kwargs["provider"] == "anthropic"
    assert call.await_args.kwargs["model"] == "claude-sonnet-4-6"
    assert call.await_args.kwargs["timeout"] == 12
    assert call.await_args.kwargs["max_tokens"] == 300


async def test_empty_rolling_summary_returns_empty_without_llm(monkeypatch):
    call = AsyncMock(return_value="unused")
    monkeypatch.setattr(generator_module, "call_text", call)

    result = await SessionSummaryGenerator(SETTINGS).generate(_ctx(""))

    assert result.text == ""
    call.assert_not_awaited()


async def test_whitespace_rolling_summary_returns_empty_without_llm(monkeypatch):
    call = AsyncMock(return_value="unused")
    monkeypatch.setattr(generator_module, "call_text", call)

    result = await SessionSummaryGenerator(SETTINGS).generate(_ctx("   \n"))

    assert result.text == ""
    call.assert_not_awaited()


async def test_timeout_propagates(monkeypatch):
    monkeypatch.setattr(
        generator_module,
        "call_text",
        AsyncMock(side_effect=LLMTimeout("too slow")),
    )

    with pytest.raises(LLMTimeout):
        await SessionSummaryGenerator(SETTINGS).generate(_ctx())
