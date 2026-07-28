"""
Entity-description merge LLM wrapper.

Small (gpt-5.1-class) model; one call per re-encountered entity that
already has an active row for this person AND whose existing and new
descriptions genuinely diverge. Mirrors :mod:`trait_merge_llm` — the
prevention-layer-1 reuse path keeps the existing entity row, and this
blends the existing and freshly-extracted descriptions into one cohesive
factual description so later mentions enrich (rather than overwrite or
drop) the entity blurb.

Reuses :class:`TraitMergeLLMConfig` (same provider/model/timeout/tokens
shape) so no new settings plumbing is required. Called by the worker
before persistence opens its transaction; the slow call stays outside
the lock.
"""

from __future__ import annotations

import asyncio

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from flashback.llm.errors import LLMMalformedResponse
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm import ToolSpec

from .trait_merge_llm import TraitMergeLLMConfig

log = structlog.get_logger("flashback.workers.extraction.entity_merge_llm")


ENTITY_MERGE_SYSTEM_PROMPT = """You merge two short descriptions of the SAME entity \
(a person, place, object, or organization from someone's life story) into ONE \
cohesive, factual description.

Rules:
- Output 1-2 sentences. Preserve concrete detail from BOTH descriptions; do not \
drop specifics (names, places, roles, relationships).
- Stay factual and about the entity itself. Do not invent anything not present \
in either description.
- If the two say the same thing, return the richer phrasing — do not pad.
- No preamble, no quotes, no mention of "description" or these instructions."""


ENTITY_MERGE_TOOL = ToolSpec(
    name="merge_entity_description",
    description="Return one cohesive factual description blending both inputs.",
    input_schema={
        "type": "object",
        "properties": {"merged_description": {"type": "string"}},
        "required": ["merged_description"],
        "additionalProperties": False,
    },
)


class _EntityMergeToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merged_description: str


def merge_entity_description(
    *,
    cfg: TraitMergeLLMConfig,
    settings,
    subject_name: str,
    entity_name: str,
    entity_kind: str,
    existing_description: str,
    new_description: str,
) -> str:
    """Run the entity-description merge LLM and return the merged string."""
    user_message = "\n".join(
        [
            f"<subject>{xml_text(subject_name)}</subject>",
            f"<entity_name>{xml_text(entity_name)}</entity_name>",
            f"<entity_kind>{xml_text(entity_kind)}</entity_kind>",
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
    args = asyncio.run(
        call_with_tool(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=ENTITY_MERGE_SYSTEM_PROMPT,
            user_message=user_message,
            tool=ENTITY_MERGE_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
            feature="entity_merge",
        )
    )
    try:
        parsed = _EntityMergeToolArgs.model_validate(args)
    except ValidationError as exc:
        raise LLMMalformedResponse(
            f"merge_entity_description response failed schema validation: {exc}"
        ) from exc
    merged = parsed.merged_description.strip()
    log.info(
        "entity_merge.completed",
        entity_name=entity_name,
        kind=entity_kind,
        existing_chars=len(existing_description),
        new_chars=len(new_description),
        merged_chars=len(merged),
    )
    return merged
