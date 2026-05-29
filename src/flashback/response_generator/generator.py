"""Response Generator implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator

from flashback.llm.interface import Provider, call_text, call_text_stream
from flashback.response_generator.context import (
    render_first_time_opener_context,
    render_starter_context,
    render_turn_context,
)
from flashback.response_generator.prompts import (
    FIRST_TIME_OPENER_PROMPT,
    INTENT_TO_PROMPT,
    STARTER_OPENER_PROMPT,
    VOICE_MODE_INSTRUCTIONS,
)
from flashback.response_generator.schema import (
    FirstTimeOpenerContext,
    ResponseResult,
    StarterContext,
    TurnContext,
)


def _apply_mode(system_prompt: str, mode: str) -> str:
    """Append the voice-mode instructions when ``mode == 'voice'``.

    Voice mode is additive — every existing prompt stays untouched and
    text mode is the no-op default.
    """
    if mode == "voice":
        return system_prompt + VOICE_MODE_INSTRUCTIONS
    return system_prompt


class ResponseGenerator:
    """Generate short, intent-shaped prose for Flashback conversations."""

    def __init__(
        self,
        settings,
        provider: Provider,
        model: str,
        timeout: float,
        max_tokens: int,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens

    async def generate_turn_response(self, ctx: TurnContext) -> ResponseResult:
        system_prompt = _apply_mode(INTENT_TO_PROMPT[ctx.intent], ctx.mode)
        user_message = render_turn_context(ctx)
        text = await call_text(
            provider=self._provider,
            model=self._model,
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            settings=self._settings,
        )
        return ResponseResult(text=text.strip())

    async def generate_starter_opener(self, ctx: StarterContext) -> ResponseResult:
        user_message = render_starter_context(ctx)
        text = await call_text(
            provider=self._provider,
            model=self._model,
            system_prompt=_apply_mode(STARTER_OPENER_PROMPT, ctx.mode),
            user_message=user_message,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            settings=self._settings,
        )
        return ResponseResult(text=text.strip())

    async def generate_first_time_opener(
        self, ctx: FirstTimeOpenerContext
    ) -> ResponseResult:
        user_message = render_first_time_opener_context(ctx)
        text = await call_text(
            provider=self._provider,
            model=self._model,
            system_prompt=_apply_mode(FIRST_TIME_OPENER_PROMPT, ctx.mode),
            user_message=user_message,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            settings=self._settings,
        )
        return ResponseResult(text=text.strip())

    async def stream_turn_response(
        self, ctx: TurnContext
    ) -> AsyncIterator[str]:
        system_prompt = _apply_mode(INTENT_TO_PROMPT[ctx.intent], ctx.mode)
        user_message = render_turn_context(ctx)
        async for chunk in call_text_stream(
            provider=self._provider,
            model=self._model,
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            settings=self._settings,
        ):
            yield chunk

    async def stream_starter_opener(
        self, ctx: StarterContext
    ) -> AsyncIterator[str]:
        user_message = render_starter_context(ctx)
        async for chunk in call_text_stream(
            provider=self._provider,
            model=self._model,
            system_prompt=_apply_mode(STARTER_OPENER_PROMPT, ctx.mode),
            user_message=user_message,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            settings=self._settings,
        ):
            yield chunk

    async def stream_first_time_opener(
        self, ctx: FirstTimeOpenerContext
    ) -> AsyncIterator[str]:
        user_message = render_first_time_opener_context(ctx)
        async for chunk in call_text_stream(
            provider=self._provider,
            model=self._model,
            system_prompt=_apply_mode(FIRST_TIME_OPENER_PROMPT, ctx.mode),
            user_message=user_message,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            settings=self._settings,
        ):
            yield chunk
