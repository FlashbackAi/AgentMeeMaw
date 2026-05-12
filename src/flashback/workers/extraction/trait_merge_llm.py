"""
Trait-description merge LLM wrapper.

Small (gpt-5.1-class) model; one call per re-encountered trait that
already has an active row for this person. Used to evolve the existing
trait description into a cohesive 1-2 sentence behavior-focused
description that incorporates the new observation.

Called by the Extraction Worker after the big extraction call and
``drop_orphan_traits`` filter, but before persistence opens its
transaction. Slow calls stay outside the transaction.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from flashback.llm.errors import LLMMalformedResponse
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text

from .prompts import TRAIT_MERGE_SYSTEM_PROMPT, TRAIT_MERGE_TOOL

log = structlog.get_logger("flashback.workers.extraction.trait_merge_llm")


@dataclass
class TraitMergeLLMConfig:
    provider: str
    model: str
    timeout: float
    max_tokens: int


class _TraitMergeToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merged_description: str


def merge_trait_description(
    *,
    cfg: TraitMergeLLMConfig,
    settings,
    subject_name: str,
    trait_name: str,
    existing_description: str,
    new_description: str,
) -> str:
    """Run the trait-merge LLM and return the merged description string."""
    user_message = _build_user_message(
        subject_name=subject_name,
        trait_name=trait_name,
        existing_description=existing_description,
        new_description=new_description,
    )
    args = asyncio.run(
        call_with_tool(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=TRAIT_MERGE_SYSTEM_PROMPT,
            user_message=user_message,
            tool=TRAIT_MERGE_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
    )
    try:
        parsed = _TraitMergeToolArgs.model_validate(args)
    except ValidationError as exc:
        raise LLMMalformedResponse(
            f"trait_merge response failed schema validation: {exc}"
        ) from exc
    merged = parsed.merged_description.strip()
    log.info(
        "trait_merge.completed",
        trait_name=trait_name,
        existing_chars=len(existing_description),
        new_chars=len(new_description),
        merged_chars=len(merged),
    )
    return merged


def _build_user_message(
    *,
    subject_name: str,
    trait_name: str,
    existing_description: str,
    new_description: str,
) -> str:
    return "\n".join(
        [
            f"<subject>{xml_text(subject_name)}</subject>",
            f"<trait_name>{xml_text(trait_name)}</trait_name>",
            "",
            "<existing_description>",
            xml_text(existing_description),
            "</existing_description>",
            "",
            "<new_description>",
            xml_text(new_description),
            "</new_description>",
        ]
    )
