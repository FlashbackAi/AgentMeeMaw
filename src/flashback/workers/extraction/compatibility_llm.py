"""
Compatibility-check LLM wrapper.

Small (gpt-5.1-class) model; one call per refinement candidate found
by vector search. The verdict drives the persistence layer:

* ``refinement``    — supersede the existing moment with the new one.
* ``contradiction`` — keep both; log for later review.
* ``independent``   — keep both; no relationship.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from flashback.llm.errors import LLMMalformedResponse
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text

from .prompts import COMPATIBILITY_SYSTEM_PROMPT, COMPATIBILITY_TOOL
from .refinement import RefinementCandidate
from .schema import CompatibilityVerdict, ExtractedMoment

log = structlog.get_logger("flashback.workers.extraction.compatibility_llm")

@dataclass
class CompatibilityLLMConfig:
    provider: str
    model: str
    timeout: float
    max_tokens: int


@dataclass(frozen=True)
class CompatibilityResponse:
    verdict: CompatibilityVerdict
    reasoning: str


class _CompatibilityToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: CompatibilityVerdict
    reasoning: str


def judge_compatibility(
    *,
    cfg: CompatibilityLLMConfig,
    settings,
    new_moment: ExtractedMoment,
    candidate: RefinementCandidate,
) -> CompatibilityResponse:
    """Run the compatibility LLM against one candidate."""
    user_message = _build_user_message(new_moment, candidate)
    args = asyncio.run(
        call_with_tool(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=COMPATIBILITY_SYSTEM_PROMPT,
            user_message=user_message,
            tool=COMPATIBILITY_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
    )
    try:
        parsed = _CompatibilityToolArgs.model_validate(args)
    except ValidationError as exc:
        raise LLMMalformedResponse(
            f"compatibility response failed schema validation: {exc}"
        ) from exc
    verdict = parsed.verdict
    reasoning = parsed.reasoning
    log.info(
        "compatibility.verdict",
        verdict=verdict,
        candidate_id=candidate.id,
        distance=candidate.distance,
    )
    return CompatibilityResponse(verdict=verdict, reasoning=reasoning)


def _build_user_message(
    new_moment: ExtractedMoment, candidate: RefinementCandidate
) -> str:
    return "\n".join(
        [
            "<new_moment>",
            f"title: {xml_text(new_moment.title)}",
            f"narrative: {xml_text(new_moment.narrative)}",
            "</new_moment>",
            "",
            "<existing_moment>",
            f"title: {xml_text(candidate.title)}",
            f"narrative: {xml_text(candidate.narrative)}",
            f"vector_distance: {candidate.distance:.4f}",
            "</existing_moment>",
        ]
    )
