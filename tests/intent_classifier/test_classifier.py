from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from flashback.intent_classifier import classifier as classifier_module
from flashback.intent_classifier.classifier import IntentClassifier
from flashback.intent_classifier.prompts import INTENT_TOOL, SYSTEM_PROMPT
from flashback.intent_classifier.schema import IntentResult
from flashback.llm.errors import LLMError, LLMTimeout
from flashback.working_memory import Turn
from tests.intent_classifier.fixtures.sample_transcripts import SAMPLE_TRANSCRIPTS


SETTINGS = SimpleNamespace(openai_api_key="openai-key", anthropic_api_key="anthropic-key")
T0 = datetime(2026, 5, 4, tzinfo=timezone.utc)


def _classifier() -> IntentClassifier:
    return IntentClassifier(
        settings=SETTINGS,
        provider="openai",
        model="gpt-5-mini",
        timeout=8,
        max_tokens=300,
    )


def _args(**overrides):
    data = {
        "intent": "story",
        "confidence": "high",
        "emotional_temperature": "medium",
        "reasoning": "The user is narrating a memory.",
    }
    data.update(overrides)
    return data


async def test_classify_returns_intent_result(monkeypatch):
    call = AsyncMock(return_value=_args())
    monkeypatch.setattr(classifier_module, "call_with_tool", call)

    result = await _classifier().classify(
        recent_turns=SAMPLE_TRANSCRIPTS["story"],
        signals={"signal_last_user_message_length": 47},
    )

    assert result == IntentResult(**_args())
    call.assert_awaited_once()


async def test_llm_timeout_is_propagated(monkeypatch):
    monkeypatch.setattr(
        classifier_module,
        "call_with_tool",
        AsyncMock(side_effect=LLMTimeout("slow")),
    )

    with pytest.raises(LLMTimeout):
        await _classifier().classify(SAMPLE_TRANSCRIPTS["story"], {})


async def test_llm_error_is_propagated(monkeypatch):
    monkeypatch.setattr(
        classifier_module,
        "call_with_tool",
        AsyncMock(side_effect=LLMError("bad")),
    )

    with pytest.raises(LLMError):
        await _classifier().classify(SAMPLE_TRANSCRIPTS["story"], {})


async def test_only_last_six_turns_are_passed(monkeypatch):
    call = AsyncMock(return_value=_args())
    monkeypatch.setattr(classifier_module, "call_with_tool", call)
    turns = [
        Turn(role="user", content=f"msg-{i}", timestamp=T0)
        for i in range(8)
    ]

    await _classifier().classify(turns, {"signal_recent_words": "words"})

    user_message = call.await_args.kwargs["user_message"]
    assert "msg-0" not in user_message
    assert "msg-1" not in user_message
    assert "msg-2" in user_message
    assert "msg-7" in user_message


async def test_system_prompt_and_tool_are_passed(monkeypatch):
    call = AsyncMock(return_value=_args())
    monkeypatch.setattr(classifier_module, "call_with_tool", call)

    await _classifier().classify(SAMPLE_TRANSCRIPTS["story"], {})

    assert call.await_args.kwargs["system_prompt"] == SYSTEM_PROMPT
    assert call.await_args.kwargs["tool"] == INTENT_TOOL


async def test_invalid_enum_from_llm_raises_validation_error(monkeypatch):
    monkeypatch.setattr(
        classifier_module,
        "call_with_tool",
        AsyncMock(return_value=_args(intent="unknown")),
    )

    with pytest.raises(ValidationError):
        await _classifier().classify(SAMPLE_TRANSCRIPTS["story"], {})


def test_build_user_block_uses_expected_format_for_fixtures():
    block = _classifier()._build_user_block(
        SAMPLE_TRANSCRIPTS["deepen"],
        {
            "signal_last_user_message_length": 26,
            "signal_turns_in_current_segment": 2,
            "not_a_signal": "hidden",
        },
    )

    assert block == "\n".join(
        [
            "<turns>",
            "assistant: What do you miss most?",
            "user: I never got to say goodbye.",
            "</turns>",
            "<signals>",
            "last_user_message_length: 26",
            "turns_in_current_segment: 2",
            "</signals>",
        ]
    )
