"""Tests for the synth_llm wrapper (no DB)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from flashback.llm.errors import LLMError, LLMTimeout
from flashback.workers.trait_synthesizer import synth_llm as synth_mod
from flashback.workers.trait_synthesizer.schema import (
    ExistingTraitView,
    ThreadView,
    TraitSynthContext,
)

from tests.workers.trait_synthesizer.conftest import (
    failing_call_with_tool,
    queued_call_with_tool,
)
from tests.workers.trait_synthesizer.fixtures.sample_states import (
    keep_decision,
    new_trait_proposal,
    synthesis_result,
    upgrade_decision,
)


def _ctx(person_id: str = "00000000-0000-0000-0000-000000000001") -> TraitSynthContext:
    return TraitSynthContext(
        person_id=person_id,
        person_name="Subject",
        existing_traits=[
            ExistingTraitView(
                id=str(uuid4()),
                name="Generous",
                description="Always sharing",
                strength="mentioned_once",
                moment_count=1,
            ),
        ],
        threads=[
            ThreadView(
                id=str(uuid4()),
                name="Cabin summers",
                description="Recurring summers at the cabin",
                moment_count=4,
            ),
        ],
    )


def test_synth_llm_happy_path(monkeypatch, stub_synth_cfg, stub_settings) -> None:
    ctx = _ctx()
    existing_id = ctx.existing_traits[0].id
    thread_id = ctx.threads[0].id

    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                synthesis_result(
                    existing_decisions=[
                        upgrade_decision(existing_id, thread_ids=[thread_id]),
                    ],
                    new_proposals=[
                        new_trait_proposal(
                            name="Quick to laugh",
                            description="Easy laugh; lit up the room.",
                            thread_ids=[thread_id],
                        ),
                    ],
                ),
            ]
        ),
    )

    result = synth_mod.synthesize(
        cfg=stub_synth_cfg,
        settings=stub_settings,
        context=ctx,
    )
    assert len(result.existing_trait_decisions) == 1
    assert result.existing_trait_decisions[0].action == "upgrade"
    assert len(result.new_trait_proposals) == 1


def test_synth_llm_propagates_timeout(monkeypatch, stub_synth_cfg, stub_settings) -> None:
    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        failing_call_with_tool(LLMTimeout("slow")),
    )
    with pytest.raises(LLMTimeout):
        synth_mod.synthesize(
            cfg=stub_synth_cfg,
            settings=stub_settings,
            context=_ctx(),
        )


def test_synth_llm_propagates_llm_error(
    monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        failing_call_with_tool(LLMError("boom")),
    )
    with pytest.raises(LLMError):
        synth_mod.synthesize(
            cfg=stub_synth_cfg,
            settings=stub_settings,
            context=_ctx(),
        )


def test_synth_llm_malformed_uuid_raises(
    monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    """Bad UUID in tool output → Pydantic ValidationError surfaces here."""
    bad = synthesis_result(
        existing_decisions=[
            {
                "trait_id": "not-a-uuid",
                "action": "keep",
                "reasoning": "x",
            }
        ]
    )
    monkeypatch.setattr(
        synth_mod, "call_with_tool", queued_call_with_tool([bad])
    )
    with pytest.raises(ValidationError):
        synth_mod.synthesize(
            cfg=stub_synth_cfg,
            settings=stub_settings,
            context=_ctx(),
        )


def test_synth_llm_missing_required_field_raises(
    monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    """Missing top-level required field → ValidationError."""
    bad = {
        "existing_trait_decisions": [],
        # "new_trait_proposals" missing
        "overall_reasoning": "x",
    }
    monkeypatch.setattr(
        synth_mod, "call_with_tool", queued_call_with_tool([bad])
    )
    with pytest.raises(ValidationError):
        synth_mod.synthesize(
            cfg=stub_synth_cfg,
            settings=stub_settings,
            context=_ctx(),
        )


def test_synth_llm_keep_only_returns_empty_actions(
    monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    """Empty proposals + keep-only decisions is the ``no change`` shape."""
    ctx = _ctx()
    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                synthesis_result(
                    existing_decisions=[
                        keep_decision(ctx.existing_traits[0].id),
                    ],
                    new_proposals=[],
                ),
            ]
        ),
    )
    result = synth_mod.synthesize(
        cfg=stub_synth_cfg,
        settings=stub_settings,
        context=ctx,
    )
    assert all(d.action == "keep" for d in result.existing_trait_decisions)
    assert result.new_trait_proposals == []
