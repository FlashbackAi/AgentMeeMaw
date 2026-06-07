"""Provider-agnostic LLM calls."""

from __future__ import annotations

import json
import asyncio
import random
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal

import anthropic
import openai
import structlog

from flashback.llm.clients import get_anthropic_client, get_openai_client
from flashback.llm.errors import LLMError, LLMMalformedResponse, LLMTimeout
from flashback.llm.tool_spec import ToolSpec

Provider = Literal["openai", "anthropic"]

log = structlog.get_logger("flashback.llm.interface")


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    opened_until: float = 0.0


_CIRCUIT_LOCK = Lock()
_CIRCUIT_STATE: dict[str, _CircuitState] = {
    "OpenAI": _CircuitState(),
    "Anthropic": _CircuitState(),
}


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


async def call_text_stream(
    *,
    provider: Provider,
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int,
    timeout: float,
    settings,
) -> AsyncIterator[str]:
    """Stream prose chunks from an LLM. Yields text deltas as they arrive.

    Errors before the first delta land in the circuit breaker but are
    not retried — once we've started yielding to the caller, we can't
    safely restart the stream.
    """
    if provider == "anthropic":
        async for chunk in _call_anthropic_text_stream(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=max_tokens,
            timeout=timeout,
            settings=settings,
        ):
            yield chunk
        return
    if provider == "openai":
        async for chunk in _call_openai_text_stream(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=max_tokens,
            timeout=timeout,
            settings=settings,
        ):
            yield chunk
        return
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
        response = await _with_provider_retries(
            lambda: client.messages.create(
                model=model,
                system=_anthropic_cached_system(system_prompt),
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
                **_anthropic_request_kwargs(settings),
            ),
            provider="Anthropic",
            settings=settings,
        )
    except anthropic.APITimeoutError as e:
        raise LLMTimeout(str(e)) from e
    except anthropic.APIError as e:
        raise LLMError(f"Anthropic API error: {e}") from e

    if getattr(response, "stop_reason", None) == "max_tokens":
        # Output hit the token ceiling mid-tool-call. The JSON may be
        # truncated — moments can reference entity indexes that never landed.
        # Downstream sanitization (extraction schema) drops the dangling
        # edges, but surface it so we can see when the ceiling is too low.
        log.warning(
            "llm.tool_call_truncated",
            provider="anthropic",
            model=model,
            tool=tool.name,
            max_tokens=max_tokens,
        )

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
        response = await _with_provider_retries(
            lambda: client.messages.create(
                model=model,
                system=_anthropic_cached_system(system_prompt),
                messages=[{"role": "user", "content": user_message}],
                max_tokens=max_tokens,
                timeout=timeout,
                **_anthropic_request_kwargs(settings),
            ),
            provider="Anthropic",
            settings=settings,
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
    """OpenAI Chat Completions API with direct JSON schema output."""
    client = get_openai_client(settings)
    try:
        response = await _with_provider_retries(
            lambda: client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": _openai_json_system_prompt(system_prompt, tool),
                    },
                    {"role": "user", "content": user_message},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": tool.name,
                        "description": tool.description,
                        "schema": tool.input_schema,
                        "strict": False,
                    },
                },
                reasoning_effort=_openai_reasoning_effort(model),
                max_completion_tokens=max_tokens,
                timeout=timeout,
                **_openai_request_kwargs(settings),
            ),
            provider="OpenAI",
            settings=settings,
        )
    except openai.APITimeoutError as e:
        raise LLMTimeout(str(e)) from e
    except openai.APIError as e:
        raise LLMError(f"OpenAI API error: {e}") from e

    try:
        msg = response.choices[0].message
    except (AttributeError, IndexError) as e:
        raise LLMMalformedResponse(f"expected chat choice, got: {response!r}") from e

    content = getattr(msg, "content", None)
    if not content:
        refusal = getattr(msg, "refusal", None)
        detail = f" refusal={refusal!r}" if refusal else ""
        raise LLMMalformedResponse(f"expected JSON content for {tool.name!r}.{detail}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMMalformedResponse(f"could not parse JSON response: {e}") from e
    if not isinstance(parsed, dict):
        raise LLMMalformedResponse(
            f"expected JSON object for {tool.name!r}, got: {type(parsed).__name__}"
        )
    return parsed


def _openai_json_system_prompt(system_prompt: str, tool: ToolSpec) -> str:
    """Override tool-call wording when OpenAI returns schema JSON directly."""

    return "\n\n".join(
        [
            system_prompt,
            (
                "For this OpenAI request, do not emit a tool call. Return only a "
                f"JSON object matching the `{tool.name}` schema. Do not wrap it "
                "in markdown or prose."
            ),
        ]
    )


def _openai_reasoning_effort(model: str) -> str:
    """Use true no-reasoning mode where supported, else the lowest GPT-5 mode."""

    if model.startswith("gpt-5.1"):
        return "none"
    return "minimal"


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
        response = await _with_provider_retries(
            lambda: client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_completion_tokens=max_tokens,
                timeout=timeout,
                **_openai_request_kwargs(settings),
            ),
            provider="OpenAI",
            settings=settings,
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


async def _call_anthropic_text_stream(
    *,
    model,
    system_prompt,
    user_message,
    max_tokens,
    timeout,
    settings,
) -> AsyncIterator[str]:
    """Anthropic Messages API streaming. Yields text deltas as they arrive."""
    client = get_anthropic_client(settings)
    _raise_if_circuit_open("Anthropic")
    first_chunk_seen = False
    try:
        async with client.messages.stream(
            model=model,
            system=_anthropic_cached_system(system_prompt),
            messages=[{"role": "user", "content": user_message}],
            max_tokens=max_tokens,
            timeout=timeout,
            **_anthropic_request_kwargs(settings),
        ) as stream:
            async for chunk in stream.text_stream:
                if chunk:
                    if not first_chunk_seen:
                        _record_provider_success("Anthropic")
                        first_chunk_seen = True
                    yield chunk
    except anthropic.APITimeoutError as e:
        if not first_chunk_seen:
            _record_provider_failure("Anthropic", settings=settings)
        raise LLMTimeout(str(e)) from e
    except anthropic.APIError as e:
        if not first_chunk_seen:
            _record_provider_failure("Anthropic", settings=settings)
        raise LLMError(f"Anthropic API error: {e}") from e
    if not first_chunk_seen:
        _record_provider_failure("Anthropic", settings=settings)
        raise LLMMalformedResponse("anthropic stream produced no text")


async def _call_openai_text_stream(
    *,
    model,
    system_prompt,
    user_message,
    max_tokens,
    timeout,
    settings,
) -> AsyncIterator[str]:
    """OpenAI Chat Completions streaming. Yields content deltas as they arrive."""
    client = get_openai_client(settings)
    _raise_if_circuit_open("OpenAI")
    first_chunk_seen = False
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_completion_tokens=max_tokens,
            timeout=timeout,
            stream=True,
            **_openai_request_kwargs(settings),
        )
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content
            except (AttributeError, IndexError):
                continue
            if delta:
                if not first_chunk_seen:
                    _record_provider_success("OpenAI")
                    first_chunk_seen = True
                yield delta
    except openai.APITimeoutError as e:
        if not first_chunk_seen:
            _record_provider_failure("OpenAI", settings=settings)
        raise LLMTimeout(str(e)) from e
    except openai.APIError as e:
        if not first_chunk_seen:
            _record_provider_failure("OpenAI", settings=settings)
        raise LLMError(f"OpenAI API error: {e}") from e
    if not first_chunk_seen:
        _record_provider_failure("OpenAI", settings=settings)
        raise LLMMalformedResponse("openai stream produced no text")


def _anthropic_cached_system(system_prompt: str) -> list[dict]:
    """Mark stable system prompts cacheable for Anthropic prompt caching."""
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


async def _with_provider_retries(
    factory: Callable[[], Awaitable],
    *,
    provider: str,
    settings,
    attempts: int = 3,
    base_delay: float = 0.35,
):
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        _raise_if_circuit_open(provider)
        try:
            result = await factory()
            _record_provider_success(provider)
            return result
        except (anthropic.APITimeoutError, openai.APITimeoutError) as exc:
            last_exc = exc
            _record_provider_failure(provider, settings=settings)
            if attempt == attempts:
                raise LLMTimeout(str(exc)) from exc
        except (anthropic.APIError, openai.APIError) as exc:
            last_exc = exc
            if not _is_retryable_provider_error(exc):
                _record_provider_failure(provider, settings=settings, retryable=False)
                raise LLMError(f"{provider} API error: {exc}") from exc
            _record_provider_failure(provider, settings=settings)
            if attempt == attempts:
                raise LLMError(f"{provider} API error: {exc}") from exc
        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, base_delay)
        await asyncio.sleep(delay)

    raise LLMError(f"{provider} API error: {last_exc}") from last_exc


def _is_retryable_provider_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 429}:
        return True
    if isinstance(status_code, int) and status_code >= 500:
        return True
    return isinstance(
        exc,
        (
            anthropic.APIConnectionError,
            openai.APIConnectionError,
        ),
    )


def _anthropic_request_kwargs(settings) -> dict:
    kwargs: dict = {}
    user_id = _provider_user_id(settings)
    if user_id:
        kwargs["metadata"] = {"user_id": user_id}
    return kwargs


def _openai_request_kwargs(settings) -> dict:
    kwargs: dict = {}
    store_enabled = _provider_store_enabled(settings)
    if hasattr(settings, "llm_provider_store_enabled"):
        kwargs["store"] = store_enabled
    user_id = _provider_user_id(settings)
    if store_enabled and user_id:
        kwargs["metadata"] = {"user_id": user_id}
    return kwargs


def _provider_user_id(settings) -> str | None:
    if not hasattr(settings, "llm_provider_user_id"):
        return None
    value = getattr(settings, "llm_provider_user_id")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _provider_store_enabled(settings) -> bool:
    if not hasattr(settings, "llm_provider_store_enabled"):
        return False
    return bool(getattr(settings, "llm_provider_store_enabled"))


def _circuit_failure_threshold(settings) -> int:
    return max(1, int(getattr(settings, "llm_circuit_breaker_failure_threshold", 5)))


def _circuit_open_seconds(settings) -> float:
    return max(0.0, float(getattr(settings, "llm_circuit_breaker_open_seconds", 30.0)))


def _raise_if_circuit_open(provider: str) -> None:
    with _CIRCUIT_LOCK:
        state = _CIRCUIT_STATE.setdefault(provider, _CircuitState())
        if state.opened_until > monotonic():
            remaining = round(state.opened_until - monotonic(), 2)
            raise LLMError(
                f"{provider} circuit breaker open; retry after {remaining}s"
            )


def _record_provider_success(provider: str) -> None:
    with _CIRCUIT_LOCK:
        state = _CIRCUIT_STATE.setdefault(provider, _CircuitState())
        state.consecutive_failures = 0
        state.opened_until = 0.0


def _record_provider_failure(
    provider: str,
    *,
    settings,
    retryable: bool = True,
) -> None:
    with _CIRCUIT_LOCK:
        state = _CIRCUIT_STATE.setdefault(provider, _CircuitState())
        if retryable:
            state.consecutive_failures += 1
        else:
            state.consecutive_failures = _circuit_failure_threshold(settings)
        threshold = _circuit_failure_threshold(settings)
        if state.consecutive_failures >= threshold:
            state.opened_until = monotonic() + _circuit_open_seconds(settings)
