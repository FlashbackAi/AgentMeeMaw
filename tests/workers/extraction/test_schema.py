"""Pydantic-validation tests for ExtractionResult and friends."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flashback.workers.extraction.schema import ExtractionResult
from tests.workers.extraction.fixtures import sample_extractions


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


def test_out_of_range_entity_index_rejected() -> None:
    payload = sample_extractions.empty_extraction()
    payload["moments"] = [
        {
            "title": "x",
            "narrative": "y",
            "generation_prompt": "z",
            "involves_entity_indexes": [5],  # no entities
        }
    ]
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)


def test_self_referential_entity_rejected() -> None:
    payload = sample_extractions.empty_extraction()
    payload["entities"] = [
        {
            "kind": "person",
            "name": "X",
            "generation_prompt": "p",
            "related_to_entity_indexes": [0],  # self-ref
        }
    ]
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)
