"""Session Summary Generator implementation."""

from __future__ import annotations

from typing import cast

from flashback.llm.interface import Provider, call_text
from flashback.session_summary.prompts import SYSTEM_PROMPT
from flashback.session_summary.schema import (
    SessionSummaryContext,
    SessionSummaryResult,
)


class SessionSummaryGenerator:
    """Generate the short fragment returned by ``/session/wrap``."""

    def __init__(self, settings) -> None:
        self._settings = settings

    async def generate(self, ctx: SessionSummaryContext) -> SessionSummaryResult:
        if not ctx.rolling_summary.strip():
            return SessionSummaryResult(text="")

        text = await call_text(
            provider=cast(Provider, self._settings.llm_session_summary_provider),
            model=self._settings.llm_session_summary_model,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._render_context(ctx),
            max_tokens=self._settings.llm_session_summary_max_tokens,
            timeout=self._settings.llm_session_summary_timeout_seconds,
            settings=self._settings,
        )
        return SessionSummaryResult(text=text.strip())

    def _render_context(self, ctx: SessionSummaryContext) -> str:
        return f"""\
<subject>
Name: {ctx.person_name}
Relationship to contributor: {ctx.relationship or 'not specified'}
</subject>

<rolling_summary>
{ctx.rolling_summary}
</rolling_summary>"""
