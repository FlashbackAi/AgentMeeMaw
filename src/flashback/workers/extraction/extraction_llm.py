"""
Extraction LLM wrapper.

The wrapper exists so the worker has a single sync surface for the big
extraction call. The underlying :func:`flashback.llm.interface.call_with_tool`
is async — we run it via ``asyncio.run`` per call, which is fine for a
worker that processes one segment at a time.

Pydantic validation runs immediately after the tool call returns. Tool
arguments that violate the schema raise :class:`pydantic.ValidationError`,
which the worker treats as an extraction failure (no SQS ack).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable

import structlog

from flashback.llm.interface import call_with_tool

from .prompts import EXTRACTION_SYSTEM_PROMPT, EXTRACTION_TOOL
from .schema import ExtractionResult, SegmentTurn

log = structlog.get_logger("flashback.workers.extraction.extraction_llm")


@dataclass
class ExtractionLLMConfig:
    provider: str
    model: str
    timeout: float
    max_tokens: int


def run_extraction(
    *,
    cfg: ExtractionLLMConfig,
    settings,
    subject_name: str,
    subject_relationship: str | None,
    prior_rolling_summary: str,
    segment_turns: Iterable[SegmentTurn],
) -> ExtractionResult:
    """
    Synchronous entry point. Returns a validated :class:`ExtractionResult`.

    Raises whatever :func:`call_with_tool` raises (LLMTimeout, LLMError,
    LLMMalformedResponse) plus ``pydantic.ValidationError`` on bad shapes.
    """
    user_message = _build_user_message(
        subject_name=subject_name,
        subject_relationship=subject_relationship,
        prior_rolling_summary=prior_rolling_summary,
        segment_turns=segment_turns,
    )

    args = asyncio.run(
        call_with_tool(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_message=user_message,
            tool=EXTRACTION_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
    )
    result = ExtractionResult.model_validate(args)
    log.info(
        "extraction.llm_returned",
        moments=len(result.moments),
        entities=len(result.entities),
        traits=len(result.traits),
        dropped_references=len(result.dropped_references),
    )
    return result


def _build_user_message(
    *,
    subject_name: str,
    subject_relationship: str | None,
    prior_rolling_summary: str,
    segment_turns: Iterable[SegmentTurn],
) -> str:
    """
    Render subject / prior summary / segment turns into a single prompt.

    The shape mirrors the segment_detector user-message format so the
    contributor sees the same structure across LLM calls.
    """
    rel = (
        f" (the contributor's {subject_relationship})"
        if subject_relationship
        else ""
    )
    lines: list[str] = [
        f"<subject>{subject_name}{rel}</subject>",
        "",
        "<prior_rolling_summary>",
        prior_rolling_summary or "",
        "</prior_rolling_summary>",
        "",
        "<closed_segment>",
    ]
    for turn in segment_turns:
        lines.append(f"{turn.role}: {turn.content}")
    lines.append("</closed_segment>")
    return "\n".join(lines)
