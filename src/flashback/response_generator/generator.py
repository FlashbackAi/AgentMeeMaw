"""Response Generator implementation."""

from __future__ import annotations

from flashback.llm.interface import Provider, call_text
from flashback.response_generator.context import (
    render_starter_context,
    render_turn_context,
)
from flashback.response_generator.prompts import (
    INTENT_TO_PROMPT,
    STARTER_OPENER_PROMPT,
)
from flashback.response_generator.schema import (
    ResponseResult,
    StarterContext,
    TurnContext,
)


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
        system_prompt = INTENT_TO_PROMPT[ctx.intent]
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
            system_prompt=STARTER_OPENER_PROMPT,
            user_message=user_message,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            settings=self._settings,
        )
        return ResponseResult(text=text.strip())
