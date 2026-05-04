from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flashback.llm.errors import LLMError
from flashback.response_generator import generator as generator_module
from flashback.response_generator.generator import ResponseGenerator
from flashback.response_generator.prompts import INTENT_TO_PROMPT, STARTER_OPENER_PROMPT
from tests.response_generator.fixtures.sample_contexts import (
    sample_starter_context,
    sample_turn_context,
)

SETTINGS = SimpleNamespace(openai_api_key="openai-key", anthropic_api_key="anthropic-key")


def _generator() -> ResponseGenerator:
    return ResponseGenerator(
        settings=SETTINGS,
        provider="anthropic",
        model="claude-sonnet-4-6",
        timeout=12,
        max_tokens=400,
    )


@pytest.mark.parametrize("intent", list(INTENT_TO_PROMPT))
async def test_generate_turn_response_uses_matching_prompt(monkeypatch, intent):
    call = AsyncMock(return_value="  A quiet reply.  ")
    monkeypatch.setattr(generator_module, "call_text", call)

    result = await _generator().generate_turn_response(sample_turn_context(intent))

    assert result.text == "A quiet reply."
    assert call.await_args.kwargs["system_prompt"] == INTENT_TO_PROMPT[intent]
    assert call.await_args.kwargs["provider"] == "anthropic"
    assert call.await_args.kwargs["model"] == "claude-sonnet-4-6"
    assert call.await_args.kwargs["timeout"] == 12
    assert call.await_args.kwargs["max_tokens"] == 400


async def test_generate_starter_opener_uses_starter_prompt(monkeypatch):
    call = AsyncMock(return_value="  Maya comes to mind here.  ")
    monkeypatch.setattr(generator_module, "call_text", call)

    result = await _generator().generate_starter_opener(sample_starter_context())

    assert result.text == "Maya comes to mind here."
    assert call.await_args.kwargs["system_prompt"] == STARTER_OPENER_PROMPT


async def test_llm_error_propagates(monkeypatch):
    monkeypatch.setattr(
        generator_module,
        "call_text",
        AsyncMock(side_effect=LLMError("response failed")),
    )

    with pytest.raises(LLMError):
        await _generator().generate_turn_response(sample_turn_context("story"))
