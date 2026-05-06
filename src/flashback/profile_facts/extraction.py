"""Profile-fact extraction LLM call.

Synchronous wrapper around the async :func:`call_with_tool`. Designed to
slot into the profile_summary worker (which is sync).

Output: a list of :class:`ExtractedFact` objects, validated by pydantic.
The runner is responsible for filtering by confidence and applying the
per-session insert/update cap (5 max).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from pydantic import ValidationError

from flashback.llm.errors import LLMMalformedResponse
from flashback.llm.interface import call_with_tool

from .prompts import PROFILE_FACTS_TOOL, SYSTEM_PROMPT
from .schema import ExtractedFact

log = structlog.get_logger("flashback.profile_facts.extraction")
PROFILE_FACTS_PROMPT_VERSION = "profile_facts.v1"


# Per-session ceiling. The LLM tool schema also caps at 5; this is the
# code-side belt-and-suspenders enforcement.
MAX_FACTS_PER_RUN: int = 5


@dataclass
class FactExtractionConfig:
    provider: str
    model: str
    timeout: float
    max_tokens: int
    max_facts_per_run: int = MAX_FACTS_PER_RUN


def extract_facts(
    *,
    cfg: FactExtractionConfig,
    settings,
    rendered_context: str,
) -> list[ExtractedFact]:
    """Run the extraction LLM and return the validated facts list.

    ``rendered_context`` is the SAME string the prose-summary call gets
    — name, traits, threads, entities, time period. Reusing it avoids
    re-fetching from the DB.
    """
    raw = asyncio.run(
        call_with_tool(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=SYSTEM_PROMPT,
            user_message=rendered_context,
            tool=PROFILE_FACTS_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
    )

    facts_raw = raw.get("facts")
    if not isinstance(facts_raw, list):
        raise LLMMalformedResponse(
            f"profile fact extractor returned non-list 'facts': {type(facts_raw).__name__}"
        )

    extracted: list[ExtractedFact] = []
    for item in facts_raw[: cfg.max_facts_per_run]:
        try:
            extracted.append(ExtractedFact.model_validate(item))
        except ValidationError as exc:
            log.warning(
                "profile_facts.extracted_fact_invalid",
                error=str(exc),
                item=item,
            )
            continue

    log.info(
        "profile_facts.extraction_complete",
        returned=len(facts_raw),
        accepted=len(extracted),
    )
    return extracted
