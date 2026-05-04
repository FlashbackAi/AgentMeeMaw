"""Async SDK client factories for LLM providers."""

from __future__ import annotations

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

_anthropic: AsyncAnthropic | None = None
_openai: AsyncOpenAI | None = None


def get_anthropic_client(settings) -> AsyncAnthropic:
    """Return the process-wide Anthropic async client."""
    global _anthropic
    if _anthropic is None:
        _anthropic = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            max_retries=1,
        )
    return _anthropic


def get_openai_client(settings) -> AsyncOpenAI:
    """Return the process-wide OpenAI async client."""
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI(
            api_key=settings.openai_api_key,
            max_retries=1,
        )
    return _openai
