from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import openai
import pytest

from flashback.llm import interface
from flashback.llm.errors import LLMMalformedResponse, LLMTimeout

SETTINGS = SimpleNamespace(openai_api_key="openai-key", anthropic_api_key="anthropic-key")


def _anthropic_client(return_value=None, side_effect=None):
    create = AsyncMock(return_value=return_value, side_effect=side_effect)
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def _openai_client(return_value=None, side_effect=None):
    create = AsyncMock(return_value=return_value, side_effect=side_effect)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _anthropic_response(content=None):
    return SimpleNamespace(
        content=content
        if content is not None
        else [SimpleNamespace(type="text", text="Generated reply.")]
    )


def _openai_response(content="Generated reply."):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


async def test_call_text_anthropic_translates_wire_format(monkeypatch):
    client = _anthropic_client(return_value=_anthropic_response())
    monkeypatch.setattr(interface, "get_anthropic_client", lambda settings: client)

    result = await interface.call_text(
        provider="anthropic",
        model="claude-sonnet-4-6",
        system_prompt="system",
        user_message="user",
        max_tokens=123,
        timeout=4.5,
        settings=SETTINGS,
    )

    assert result == "Generated reply."
    kwargs = client.messages.create.await_args.kwargs
    assert kwargs == {
        "model": "claude-sonnet-4-6",
        "system": [
            {
                "type": "text",
                "text": "system",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": "user"}],
        "max_tokens": 123,
        "timeout": 4.5,
    }
    assert "tools" not in kwargs


async def test_call_text_openai_translates_wire_format(monkeypatch):
    client = _openai_client(return_value=_openai_response())
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)

    result = await interface.call_text(
        provider="openai",
        model="gpt-5.1",
        system_prompt="system",
        user_message="user",
        max_tokens=123,
        timeout=4.5,
        settings=SETTINGS,
    )

    assert result == "Generated reply."
    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs == {
        "model": "gpt-5.1",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "max_completion_tokens": 123,
        "timeout": 4.5,
    }
    assert "tools" not in kwargs


async def test_anthropic_empty_content_response_is_malformed(monkeypatch):
    client = _anthropic_client(return_value=_anthropic_response(content=[]))
    monkeypatch.setattr(interface, "get_anthropic_client", lambda settings: client)

    with pytest.raises(LLMMalformedResponse):
        await interface.call_text(
            provider="anthropic",
            model="model",
            system_prompt="system",
            user_message="user",
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )


async def test_openai_null_content_response_is_malformed(monkeypatch):
    client = _openai_client(return_value=_openai_response(content=None))
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)

    with pytest.raises(LLMMalformedResponse):
        await interface.call_text(
            provider="openai",
            model="model",
            system_prompt="system",
            user_message="user",
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )


async def test_anthropic_text_timeout_maps_to_llm_timeout(monkeypatch):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    client = _anthropic_client(side_effect=anthropic.APITimeoutError(request=request))
    monkeypatch.setattr(interface, "get_anthropic_client", lambda settings: client)

    with pytest.raises(LLMTimeout):
        await interface.call_text(
            provider="anthropic",
            model="model",
            system_prompt="system",
            user_message="user",
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )


async def test_openai_text_timeout_maps_to_llm_timeout(monkeypatch):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    client = _openai_client(side_effect=openai.APITimeoutError(request=request))
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)

    with pytest.raises(LLMTimeout):
        await interface.call_text(
            provider="openai",
            model="model",
            system_prompt="system",
            user_message="user",
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )
