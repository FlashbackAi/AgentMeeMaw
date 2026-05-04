"""Reusable input shapes for Trait Synthesizer tests.

These dicts mirror the LLM tool's schema so callers can feed them into
``queued_call_with_tool`` or ``TraitSynthesisResult.model_validate``.
"""

from __future__ import annotations

from uuid import UUID, uuid4


def keep_decision(trait_id: UUID | str, *, reasoning: str = "no change") -> dict:
    return {
        "trait_id": str(trait_id),
        "action": "keep",
        "reasoning": reasoning,
        "supporting_thread_ids": [],
    }


def upgrade_decision(
    trait_id: UUID | str,
    *,
    thread_ids: list[UUID | str] | None = None,
    reasoning: str = "more evidence accumulated",
) -> dict:
    return {
        "trait_id": str(trait_id),
        "action": "upgrade",
        "reasoning": reasoning,
        "supporting_thread_ids": [str(t) for t in (thread_ids or [])],
    }


def downgrade_decision(
    trait_id: UUID | str,
    *,
    thread_ids: list[UUID | str] | None = None,
    reasoning: str = "evidence is thinner than the strength suggests",
) -> dict:
    return {
        "trait_id": str(trait_id),
        "action": "downgrade",
        "reasoning": reasoning,
        "supporting_thread_ids": [str(t) for t in (thread_ids or [])],
    }


def new_trait_proposal(
    *,
    name: str,
    description: str,
    initial_strength: str = "moderate",
    thread_ids: list[UUID | str] | None = None,
    reasoning: str = "supported by multiple threads",
) -> dict:
    return {
        "name": name,
        "description": description,
        "initial_strength": initial_strength,
        "supporting_thread_ids": [str(t) for t in (thread_ids or [str(uuid4())])],
        "reasoning": reasoning,
    }


def synthesis_result(
    *,
    existing_decisions: list[dict] | None = None,
    new_proposals: list[dict] | None = None,
    overall_reasoning: str = "ok",
) -> dict:
    return {
        "existing_trait_decisions": existing_decisions or [],
        "new_trait_proposals": new_proposals or [],
        "overall_reasoning": overall_reasoning,
    }
