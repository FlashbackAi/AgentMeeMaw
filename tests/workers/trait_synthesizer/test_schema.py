"""Tests for the Trait Synthesizer pydantic / dataclass models."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from flashback.workers.trait_synthesizer.schema import (
    STRENGTH_LADDER,
    ExistingTraitDecision,
    NewTraitProposal,
    TraitSynthesisResult,
)

from tests.workers.trait_synthesizer.fixtures.sample_states import (
    keep_decision,
    new_trait_proposal,
    synthesis_result,
    upgrade_decision,
)


def test_strength_ladder_order() -> None:
    assert STRENGTH_LADDER == (
        "mentioned_once",
        "moderate",
        "strong",
        "defining",
    )


def test_existing_trait_decision_keep_valid() -> None:
    d = ExistingTraitDecision.model_validate(keep_decision(uuid4()))
    assert d.action == "keep"
    assert d.supporting_thread_ids == []


def test_existing_trait_decision_upgrade_with_threads() -> None:
    tids = [uuid4(), uuid4()]
    d = ExistingTraitDecision.model_validate(upgrade_decision(uuid4(), thread_ids=tids))
    assert d.action == "upgrade"
    assert len(d.supporting_thread_ids) == 2


def test_existing_trait_decision_invalid_action() -> None:
    with pytest.raises(ValidationError):
        ExistingTraitDecision.model_validate(
            {
                "trait_id": str(uuid4()),
                "action": "promote",  # not in {keep, upgrade, downgrade}
                "reasoning": "x",
            }
        )


def test_existing_trait_decision_bad_uuid() -> None:
    with pytest.raises(ValidationError):
        ExistingTraitDecision.model_validate(
            {
                "trait_id": "not-a-uuid",
                "action": "keep",
                "reasoning": "x",
            }
        )


def test_new_trait_proposal_requires_one_thread() -> None:
    """``min_length=1`` on ``supporting_thread_ids``."""
    with pytest.raises(ValidationError):
        NewTraitProposal.model_validate(
            {
                "name": "Generous",
                "description": "Always sharing",
                "initial_strength": "moderate",
                "supporting_thread_ids": [],
                "reasoning": "x",
            }
        )


def test_new_trait_proposal_max_name_length() -> None:
    long_name = "a" * 81
    with pytest.raises(ValidationError):
        NewTraitProposal.model_validate(
            {
                "name": long_name,
                "description": "x",
                "initial_strength": "moderate",
                "supporting_thread_ids": [str(uuid4())],
                "reasoning": "x",
            }
        )


def test_new_trait_proposal_invalid_strength() -> None:
    with pytest.raises(ValidationError):
        NewTraitProposal.model_validate(
            {
                "name": "Generous",
                "description": "x",
                "initial_strength": "godlike",
                "supporting_thread_ids": [str(uuid4())],
                "reasoning": "x",
            }
        )


def test_synthesis_result_round_trip() -> None:
    raw = synthesis_result(
        existing_decisions=[
            keep_decision(uuid4()),
            upgrade_decision(uuid4(), thread_ids=[uuid4()]),
        ],
        new_proposals=[
            new_trait_proposal(name="Quick to laugh", description="Easy laugh"),
        ],
        overall_reasoning="balanced",
    )
    parsed = TraitSynthesisResult.model_validate(raw)
    assert len(parsed.existing_trait_decisions) == 2
    assert len(parsed.new_trait_proposals) == 1
    assert parsed.overall_reasoning == "balanced"


def test_synthesis_result_extra_field_forbidden() -> None:
    raw = synthesis_result()
    raw["extra"] = "nope"
    with pytest.raises(ValidationError):
        TraitSynthesisResult.model_validate(raw)
