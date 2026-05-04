"""Single LLM call wrapper for the Profile Summary Generator.

Sync surface backed by ``asyncio.run`` around the async
:func:`flashback.llm.interface.call_text`. Mirrors the pattern in
``trait_synthesizer.synth_llm`` but uses prose text rather than
forced tool use.

Errors propagate untouched:

* ``LLMTimeout`` — caller decides whether to ack (the worker does not).
* ``LLMError`` / ``LLMMalformedResponse`` — permanent for this run;
  caller acks and moves on. We raise :class:`LLMMalformedResponse` if
  the model returns an empty / whitespace-only string.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog

from flashback.llm.errors import LLMMalformedResponse
from flashback.llm.interface import call_text

from .context import render_context
from .prompts import SYSTEM_PROMPT
from .schema import ProfileSummaryContext

log = structlog.get_logger("flashback.workers.profile_summary.summary_llm")


@dataclass
class SummaryLLMConfig:
    provider: str
    model: str
    timeout: float
    max_tokens: int


def generate_summary(
    *,
    cfg: SummaryLLMConfig,
    settings,
    context: ProfileSummaryContext,
) -> str:
    """Call the big LLM for one person and return the prose summary.

    Empty / whitespace-only output is treated as a permanent
    malformed-response error (consistent with the worker's fail-soft
    policy on permanent LLM errors).
    """
    user_message = render_context(context)
    text = asyncio.run(
        call_text(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
    )
    stripped = (text or "").strip()
    if not stripped:
        raise LLMMalformedResponse("empty profile summary returned")
    log.info(
        "profile_summary.llm_returned",
        person_id=context.person_id,
        chars=len(stripped),
    )
    return stripped
