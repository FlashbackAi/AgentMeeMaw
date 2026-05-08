from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from unittest.mock import AsyncMock

from flashback.llm import interface
from flashback.llm.errors import LLMError, LLMMalformedResponse, LLMTimeout
from flashback.llm.tool_spec import ToolSpec


TOOL = ToolSpec(
    name="classify_intent",
    description="Classify the latest turn.",
    input_schema={
        "type": "object",
        "properties": {"intent": {"type": "string"}},
        "required": ["intent"],
    },
)
SETTINGS = SimpleNamespace(openai_api_key="openai-key", anthropic_api_key="anthropic-key")


def _anthropic_client(return_value=None, side_effect=None):
    create = AsyncMock(return_value=return_value, side_effect=side_effect)
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def _openai_client(return_value=None, side_effect=None):
    create = AsyncMock(return_value=return_value, side_effect=side_effect)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _anthropic_response(name=TOOL.name, input_args=None, block_type="tool_use"):
    return SimpleNamespace(
        content=[
            SimpleNamespace(
                type=block_type,
                name=name,
                input=input_args or {"intent": "story"},
            )
        ]
    )


def _openai_response(content=None):
    if content is None:
        content = json.dumps({"intent": "story"})
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


async def test_call_with_tool_anthropic_translates_wire_format(monkeypatch):
    client = _anthropic_client(return_value=_anthropic_response())
    monkeypatch.setattr(interface, "get_anthropic_client", lambda settings: client)

    result = await interface.call_with_tool(
        provider="anthropic",
        model="claude-sonnet-4-6",
        system_prompt="system",
        user_message="user",
        tool=TOOL,
        max_tokens=123,
        timeout=4.5,
        settings=SETTINGS,
    )

    assert result == {"intent": "story"}
    client.messages.create.assert_awaited_once_with(
        model="claude-sonnet-4-6",
        system=[
            {
                "type": "text",
                "text": "system",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": "user"}],
        tools=[
            {
                "name": TOOL.name,
                "description": TOOL.description,
                "input_schema": TOOL.input_schema,
            }
        ],
        tool_choice={"type": "tool", "name": TOOL.name},
        max_tokens=123,
        timeout=4.5,
    )


async def test_call_with_tool_openai_translates_wire_format(monkeypatch):
    client = _openai_client(return_value=_openai_response())
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)

    result = await interface.call_with_tool(
        provider="openai",
        model="gpt-5.1",
        system_prompt="system",
        user_message="user",
        tool=TOOL,
        max_tokens=123,
        timeout=4.5,
        settings=SETTINGS,
    )

    assert result == {"intent": "story"}
    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs == {
        "model": "gpt-5.1",
        "messages": [
            {
                "role": "system",
                "content": (
                    "system\n\n"
                    "For this OpenAI request, do not emit a tool call. "
                    "Return only a JSON object matching the `classify_intent` "
                    "schema. Do not wrap it in markdown or prose."
                ),
            },
            {"role": "user", "content": "user"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": TOOL.name,
                "description": TOOL.description,
                "schema": TOOL.input_schema,
                "strict": False,
            },
        },
        "reasoning_effort": "none",
        "max_completion_tokens": 123,
        "timeout": 4.5,
    }
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


async def test_call_with_tool_openai_uses_none_reasoning_for_gpt_5_1(monkeypatch):
    client = _openai_client(return_value=_openai_response())
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)

    await interface.call_with_tool(
        provider="openai",
        model="gpt-5.1",
        system_prompt="system",
        user_message="user",
        tool=TOOL,
        max_tokens=123,
        timeout=4.5,
        settings=SETTINGS,
    )

    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["reasoning_effort"] == "none"


async def test_openai_omits_metadata_when_store_is_disabled(monkeypatch):
    client = _openai_client(return_value=_openai_response())
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)
    settings = SimpleNamespace(
        openai_api_key="openai-key",
        llm_provider_store_enabled=False,
        llm_provider_user_id="flashback-service",
    )

    await interface.call_with_tool(
        provider="openai",
        model="gpt-5.1",
        system_prompt="system",
        user_message="user",
        tool=TOOL,
        max_tokens=123,
        timeout=4.5,
        settings=settings,
    )

    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["store"] is False
    assert "metadata" not in kwargs


async def test_openai_includes_metadata_when_store_is_enabled(monkeypatch):
    client = _openai_client(return_value=_openai_response())
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)
    settings = SimpleNamespace(
        openai_api_key="openai-key",
        llm_provider_store_enabled=True,
        llm_provider_user_id="flashback-service",
    )

    await interface.call_with_tool(
        provider="openai",
        model="gpt-5.1",
        system_prompt="system",
        user_message="user",
        tool=TOOL,
        max_tokens=123,
        timeout=4.5,
        settings=settings,
    )

    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["store"] is True
    assert kwargs["metadata"] == {"user_id": "flashback-service"}


async def test_anthropic_timeout_maps_to_llm_timeout(monkeypatch):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    client = _anthropic_client(side_effect=anthropic.APITimeoutError(request=request))
    monkeypatch.setattr(interface, "get_anthropic_client", lambda settings: client)

    with pytest.raises(LLMTimeout):
        await interface.call_with_tool(
            provider="anthropic",
            model="model",
            system_prompt="system",
            user_message="user",
            tool=TOOL,
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )


async def test_openai_timeout_maps_to_llm_timeout(monkeypatch):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    client = _openai_client(side_effect=openai.APITimeoutError(request=request))
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)

    with pytest.raises(LLMTimeout):
        await interface.call_with_tool(
            provider="openai",
            model="model",
            system_prompt="system",
            user_message="user",
            tool=TOOL,
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )


async def test_anthropic_non_tool_use_response_is_malformed(monkeypatch):
    client = _anthropic_client(return_value=_anthropic_response(block_type="text"))
    monkeypatch.setattr(interface, "get_anthropic_client", lambda settings: client)

    with pytest.raises(LLMMalformedResponse):
        await interface.call_with_tool(
            provider="anthropic",
            model="model",
            system_prompt="system",
            user_message="user",
            tool=TOOL,
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )


async def test_openai_empty_json_response_is_malformed(monkeypatch):
    client = _openai_client(return_value=_openai_response(content=""))
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)

    with pytest.raises(LLMMalformedResponse):
        await interface.call_with_tool(
            provider="openai",
            model="model",
            system_prompt="system",
            user_message="user",
            tool=TOOL,
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )


async def test_openai_invalid_json_arguments_are_malformed(monkeypatch):
    client = _openai_client(return_value=_openai_response(content="{nope"))
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)

    with pytest.raises(LLMMalformedResponse):
        await interface.call_with_tool(
            provider="openai",
            model="model",
            system_prompt="system",
            user_message="user",
            tool=TOOL,
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )


async def test_anthropic_wrong_tool_name_is_malformed(monkeypatch):
    client = _anthropic_client(return_value=_anthropic_response(name="other_tool"))
    monkeypatch.setattr(interface, "get_anthropic_client", lambda settings: client)

    with pytest.raises(LLMMalformedResponse):
        await interface.call_with_tool(
            provider="anthropic",
            model="model",
            system_prompt="system",
            user_message="user",
            tool=TOOL,
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )


async def test_openai_non_object_json_response_is_malformed(monkeypatch):
    client = _openai_client(return_value=_openai_response(content='["story"]'))
    monkeypatch.setattr(interface, "get_openai_client", lambda settings: client)

    with pytest.raises(LLMMalformedResponse):
        await interface.call_with_tool(
            provider="openai",
            model="model",
            system_prompt="system",
            user_message="user",
            tool=TOOL,
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )


async def test_unknown_provider_is_llm_error():
    with pytest.raises(LLMError):
        await interface.call_with_tool(
            provider="unknown",
            model="model",
            system_prompt="system",
            user_message="user",
            tool=TOOL,
            max_tokens=1,
            timeout=1,
            settings=SETTINGS,
        )
