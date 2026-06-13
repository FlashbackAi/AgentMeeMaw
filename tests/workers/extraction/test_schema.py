"""Pydantic-validation tests for ExtractionResult and friends."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from flashback.workers.extraction.schema import (
    ExtractionMessage,
    ExtractionResult,
    drop_orphan_traits,
)
from tests.workers.extraction.fixtures import sample_extractions


# ---------------------------------------------------------------------------
# Minimal valid ExtractionMessage body (reused across tests)
# ---------------------------------------------------------------------------

def _minimal_extraction_message_body() -> dict:
    return {
        "session_id": str(uuid4()),
        "person_id": str(uuid4()),
        "segment_turns": [
            {
                "role": "assistant",
                "content": "Tell me about him.",
                "timestamp": "2026-05-04T12:00:00+00:00",
            },
            {
                "role": "user",
                "content": "He was warm.",
                "timestamp": "2026-05-04T12:00:30+00:00",
            },
        ],
        "rolling_summary": "",
        "prior_rolling_summary": "",
        "seeded_question_id": None,
        "is_final": False,
    }


def test_extraction_message_carries_told_by_user_id() -> None:
    body = _minimal_extraction_message_body()
    body["told_by_user_id"] = "11111111-1111-1111-1111-111111111111"
    msg = ExtractionMessage.model_validate(body)
    assert str(msg.told_by_user_id) == "11111111-1111-1111-1111-111111111111"


def test_extraction_message_told_by_user_id_defaults_to_none() -> None:
    body = _minimal_extraction_message_body()
    # told_by_user_id is deliberately absent
    msg = ExtractionMessage.model_validate(body)
    assert msg.told_by_user_id is None


def test_clean_extraction_validates() -> None:
    payload = sample_extractions.clean_extraction()
    result = ExtractionResult.model_validate(payload)
    assert len(result.moments) == 2
    assert len(result.entities) == 3
    assert len(result.traits) == 1
    assert len(result.dropped_references) == 1


def test_empty_extraction_validates() -> None:
    payload = sample_extractions.empty_extraction()
    result = ExtractionResult.model_validate(payload)
    assert result.moments == []
    assert result.entities == []
    assert result.traits == []


def test_dropped_reference_without_themes_rejected() -> None:
    payload = sample_extractions.empty_extraction()
    payload["dropped_references"] = [
        {
            "dropped_phrase": "Aunt Mavis",
            "question_text": "Who was Aunt Mavis?",
            "themes": [],  # invariant #9 violation
        }
    ]
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)


def test_out_of_range_entity_index_dropped_not_rejected() -> None:
    """A dangling entity index (the truncation signature that DLQ'd
    content-dense legacies) drops the bad edge and keeps the moment, rather
    than discarding the whole extraction. Invariant #6."""
    payload = sample_extractions.empty_extraction()
    payload["moments"] = [
        {
            "title": "x",
            "narrative": "y",
            "generation_prompt": "z",
            "involves_entity_indexes": [5],  # no entities emitted
            "happened_at_entity_index": 2,  # also dangling
        }
    ]
    result = ExtractionResult.model_validate(payload)
    assert len(result.moments) == 1
    assert result.moments[0].involves_entity_indexes == []
    assert result.moments[0].happened_at_entity_index is None


def test_partially_truncated_indexes_keep_valid_edges() -> None:
    """Valid edges survive; only the out-of-range ones are dropped — the
    real-world truncation case where moments reference entities beyond the
    array that actually landed."""
    payload = sample_extractions.empty_extraction()
    payload["entities"] = [
        {"kind": "person", "name": "Chandraiah", "generation_prompt": "p"},
        {"kind": "place", "name": "Gudi Vellagula Palli", "generation_prompt": "p"},
        {"kind": "person", "name": "Manjula", "generation_prompt": "p"},
    ]
    payload["moments"] = [
        {
            "title": "x",
            "narrative": "y",
            "generation_prompt": "z",
            "involves_entity_indexes": [0, 2, 7],  # 7 is past the truncation
            "happened_at_entity_index": 1,
        }
    ]
    result = ExtractionResult.model_validate(payload)
    assert result.moments[0].involves_entity_indexes == [0, 2]
    assert result.moments[0].happened_at_entity_index == 1


def test_self_referential_entity_edge_dropped_not_rejected() -> None:
    payload = sample_extractions.empty_extraction()
    payload["entities"] = [
        {
            "kind": "person",
            "name": "X",
            "generation_prompt": "p",
            "related_to_entity_indexes": [0, 9],  # self-ref + dangling
        }
    ]
    result = ExtractionResult.model_validate(payload)
    assert result.entities[0].related_to_entity_indexes == []


# ---------------------------------------------------------------------------
# drop_orphan_traits — invariant #18 backstop
# ---------------------------------------------------------------------------


def _result_with_traits(
    *,
    trait_names: list[str],
    moment_exemplifies: list[list[int]],
) -> ExtractionResult:
    """Build a minimal ExtractionResult with N traits and M moments where
    moments[i] has exemplifies_trait_indexes = moment_exemplifies[i]."""
    payload = sample_extractions.empty_extraction()
    payload["traits"] = [{"name": n} for n in trait_names]
    payload["moments"] = [
        {
            "title": f"m{i}",
            "narrative": "n",
            "generation_prompt": "p",
            "exemplifies_trait_indexes": idxs,
        }
        for i, idxs in enumerate(moment_exemplifies)
    ]
    return ExtractionResult.model_validate(payload)


def test_drop_orphan_traits_keeps_referenced_traits() -> None:
    """All traits referenced by a moment survive unchanged."""
    result = _result_with_traits(
        trait_names=["Kind", "Patient"],
        moment_exemplifies=[[0, 1]],
    )
    filtered, dropped = drop_orphan_traits(result)
    assert dropped == 0
    assert [t.name for t in filtered.traits] == ["Kind", "Patient"]
    assert filtered.moments[0].exemplifies_trait_indexes == [0, 1]


def test_drop_orphan_traits_drops_unreferenced_and_remaps_indexes() -> None:
    """Traits with no exemplifying moment are dropped; survivors' indexes
    are remapped so moment edges still resolve."""
    # 3 traits: "Strong" (orphan), "Kind" (referenced), "Talented" (orphan)
    result = _result_with_traits(
        trait_names=["Strong", "Kind", "Talented"],
        moment_exemplifies=[[1]],
    )
    filtered, dropped = drop_orphan_traits(result)
    assert dropped == 2
    assert [t.name for t in filtered.traits] == ["Kind"]
    # The surviving moment's exemplifies index must point at "Kind" at its
    # new position (0), not the old position (1).
    assert filtered.moments[0].exemplifies_trait_indexes == [0]


def test_drop_orphan_traits_drops_all_when_no_moment_references() -> None:
    result = _result_with_traits(
        trait_names=["Strong", "Handsome"],
        moment_exemplifies=[[]],
    )
    filtered, dropped = drop_orphan_traits(result)
    assert dropped == 2
    assert filtered.traits == []
    assert filtered.moments[0].exemplifies_trait_indexes == []


def test_drop_orphan_traits_noop_when_empty() -> None:
    result = _result_with_traits(trait_names=[], moment_exemplifies=[[]])
    filtered, dropped = drop_orphan_traits(result)
    assert dropped == 0
    assert filtered is result  # short-circuit returns the same object
