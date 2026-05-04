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


def _openai_response(name=TOOL.name, arguments=None, tool_calls=True):
    calls = None
    if tool_calls:
        calls = [
            SimpleNamespace(
                function=SimpleNamespace(
                    name=name,
                    arguments=arguments if arguments is not None else json.dumps({"intent": "story"}),
                )
            )
        ]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=calls))])


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
        system="system",
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
        model="gpt-5-mini",
        system_prompt="system",
        user_message="user",
        tool=TOOL,
        max_tokens=123,
        timeout=4.5,
        settings=SETTINGS,
    )

    assert result == {"intent": "story"}
    client.chat.completions.create.assert_awaited_once_with(
        model="gpt-5-mini",
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": TOOL.name,
                    "description": TOOL.description,
                    "parameters": TOOL.input_schema,
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": TOOL.name}},
        max_completion_tokens=123,
        timeout=4.5,
    )


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


async def test_openai_no_tool_call_response_is_malformed(monkeypatch):
    client = _openai_client(return_value=_openai_response(tool_calls=False))
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
    client = _openai_client(return_value=_openai_response(arguments="{nope"))
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


async def test_openai_wrong_tool_name_is_malformed(monkeypatch):
    client = _openai_client(return_value=_openai_response(name="other_tool"))
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
