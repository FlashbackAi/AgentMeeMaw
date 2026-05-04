"""Provider-agnostic LLM calls."""

from __future__ import annotations

import json
from typing import Literal

import anthropic
import openai

from flashback.llm.clients import get_anthropic_client, get_openai_client
from flashback.llm.errors import LLMError, LLMMalformedResponse, LLMTimeout
from flashback.llm.tool_spec import ToolSpec

Provider = Literal["openai", "anthropic"]


async def call_with_tool(
    *,
    provider: Provider,
    model: str,
    system_prompt: str,
    user_message: str,
    tool: ToolSpec,
    max_tokens: int,
    timeout: float,
    settings,
) -> dict:
    """Call an LLM with forced tool use and return parsed tool arguments."""
    if provider == "anthropic":
        return await _call_anthropic(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            tool=tool,
            max_tokens=max_tokens,
            timeout=timeout,
            settings=settings,
        )
    if provider == "openai":
        return await _call_openai(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            tool=tool,
            max_tokens=max_tokens,
            timeout=timeout,
            settings=settings,
        )
    raise LLMError(f"unknown provider: {provider!r}")


async def call_text(
    *,
    provider: Provider,
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int,
    timeout: float,
    settings,
) -> str:
    """Call an LLM for plain prose and return generated text."""
    if provider == "anthropic":
        return await _call_anthropic_text(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=max_tokens,
            timeout=timeout,
            settings=settings,
        )
    if provider == "openai":
        return await _call_openai_text(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=max_tokens,
            timeout=timeout,
            settings=settings,
        )
    raise LLMError(f"unknown provider: {provider!r}")


async def _call_anthropic(
    *,
    model,
    system_prompt,
    user_message,
    tool,
    max_tokens,
    timeout,
    settings,
) -> dict:
    """Anthropic Messages API with a required tool_use block."""
    client = get_anthropic_client(settings)
    try:
        response = await client.messages.create(
            model=model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=[
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool.name},
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except anthropic.APITimeoutError as e:
        raise LLMTimeout(str(e)) from e
    except anthropic.APIError as e:
        raise LLMError(f"Anthropic API error: {e}") from e

    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            if getattr(block, "name", None) != tool.name:
                raise LLMMalformedResponse(
                    f"expected tool {tool.name!r}, got {getattr(block, 'name', None)!r}"
                )
            return dict(block.input)
    raise LLMMalformedResponse(
        f"expected tool_use block for {tool.name!r}, got: {response.content!r}"
    )


async def _call_anthropic_text(
    *,
    model,
    system_prompt,
    user_message,
    max_tokens,
    timeout,
    settings,
) -> str:
    """Anthropic Messages API without tools."""
    client = get_anthropic_client(settings)
    try:
        response = await client.messages.create(
            model=model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except anthropic.APITimeoutError as e:
        raise LLMTimeout(str(e)) from e
    except anthropic.APIError as e:
        raise LLMError(f"Anthropic API error: {e}") from e

    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            if text:
                return str(text)
    raise LLMMalformedResponse(
        f"expected text block, got: {getattr(response, 'content', None)!r}"
    )


async def _call_openai(
    *,
    model,
    system_prompt,
    user_message,
    tool,
    max_tokens,
    timeout,
    settings,
) -> dict:
    """OpenAI Chat Completions API with a forced function tool call."""
    client = get_openai_client(settings)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
            ],
            tool_choice={
                "type": "function",
                "function": {"name": tool.name},
            },
            max_completion_tokens=max_tokens,
            timeout=timeout,
        )
    except openai.APITimeoutError as e:
        raise LLMTimeout(str(e)) from e
    except openai.APIError as e:
        raise LLMError(f"OpenAI API error: {e}") from e

    try:
        msg = response.choices[0].message
    except (AttributeError, IndexError) as e:
        raise LLMMalformedResponse(f"expected chat choice, got: {response!r}") from e

    if not getattr(msg, "tool_calls", None):
        raise LLMMalformedResponse(
            f"expected tool_calls for {tool.name!r}, got message: {msg!r}"
        )
    call = msg.tool_calls[0]
    if call.function.name != tool.name:
        raise LLMMalformedResponse(
            f"expected tool {tool.name!r}, got {call.function.name!r}"
        )
    try:
        return json.loads(call.function.arguments)
    except json.JSONDecodeError as e:
        raise LLMMalformedResponse(f"could not parse tool arguments JSON: {e}") from e


async def _call_openai_text(
    *,
    model,
    system_prompt,
    user_message,
    max_tokens,
    timeout,
    settings,
) -> str:
    """OpenAI Chat Completions API without tools."""
    client = get_openai_client(settings)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_completion_tokens=max_tokens,
            timeout=timeout,
        )
    except openai.APITimeoutError as e:
        raise LLMTimeout(str(e)) from e
    except openai.APIError as e:
        raise LLMError(f"OpenAI API error: {e}") from e

    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as e:
        raise LLMMalformedResponse(f"expected chat choice, got: {response!r}") from e
    if not content:
        raise LLMMalformedResponse(f"expected message content, got: {content!r}")
    return str(content)
