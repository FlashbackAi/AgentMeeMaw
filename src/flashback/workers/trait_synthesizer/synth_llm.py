"""Single LLM call wrapper for the Trait Synthesizer.

Sync surface backed by ``asyncio.run`` around the async
:func:`flashback.llm.interface.call_with_tool`. Mirrors the pattern in
``thread_detector.naming_llm`` and ``extraction.extraction_llm``.

Errors propagate untouched:

* ``LLMTimeout`` — caller decides whether to ack (the worker does not).
* ``LLMError`` / ``LLMMalformedResponse`` — permanent for this run;
  caller acks and moves on.
* Pydantic ``ValidationError`` from result parsing — same as
  ``LLMMalformedResponse`` (permanent).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog

from flashback.llm.interface import call_with_tool

from .context import render_user_message
from .prompts import SYNTH_TOOL, SYSTEM_PROMPT
from .schema import TraitSynthContext, TraitSynthesisResult

log = structlog.get_logger("flashback.workers.trait_synthesizer.synth_llm")


@dataclass
class SynthLLMConfig:
    provider: str
    model: str
    timeout: float
    max_tokens: int


def synthesize(
    *,
    cfg: SynthLLMConfig,
    settings,
    context: TraitSynthContext,
) -> TraitSynthesisResult:
    """Run the synthesizer LLM for one person. Returns a typed result."""
    user_message = render_user_message(context)
    args = asyncio.run(
        call_with_tool(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=SYSTEM_PROMPT,
            user_message=user_message,
            tool=SYNTH_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
    )
    result = TraitSynthesisResult.model_validate(args)
    log.info(
        "trait_synthesizer.llm_returned",
        person_id=context.person_id,
        existing_decisions=len(result.existing_trait_decisions),
        new_proposals=len(result.new_trait_proposals),
    )
    return result
