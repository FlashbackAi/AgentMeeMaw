"""
Reusable sample :class:`ExtractionResult` fixtures.

Each builder returns a dict in the exact shape the LLM tool would emit.
Tests can call ``ExtractionResult.model_validate(...)`` on it or hand it
to a stubbed ``call_with_tool``.
"""

from __future__ import annotations

from typing import Any


def clean_extraction(*, with_seeded_question: bool = False) -> dict[str, Any]:
    """A 2-moment / 3-entity / 1-trait / 1-dropped-reference extraction."""
    return {
        "moments": [
            {
                "title": "Sunday pancakes",
                "narrative": "Dad always made pancakes Sunday mornings.",
                "generation_prompt": "A wood-paneled kitchen at dawn.",
                "time_anchor": {"decade": "1990s"},
                "life_period_estimate": "childhood",
                "sensory_details": "Smell of butter and griddle smoke.",
                "emotional_tone": "warm",
                "contributor_perspective": "I felt safe.",
                "involves_entity_indexes": [0, 1],
                "happened_at_entity_index": 1,
                "exemplifies_trait_indexes": [0],
            },
            {
                "title": "Truck on the porch",
                "narrative": "His old red truck always sat in the driveway.",
                "generation_prompt": "An old red truck under a porch light.",
                "time_anchor": {"year": 1995},
                "involves_entity_indexes": [2],
                "exemplifies_trait_indexes": [],
            },
        ],
        "entities": [
            {
                "kind": "person",
                "name": "Dad",
                "generation_prompt": "A warm father figure stirring batter.",
                "description": "The contributor's father.",
                "aliases": [],
                "attributes": {"relationship": "father", "saying": "rise and shine"},
                "related_to_entity_indexes": [],
            },
            {
                "kind": "place",
                "name": "Family kitchen",
                "generation_prompt": "Wood paneled kitchen with morning light.",
                "description": "The childhood family kitchen.",
                "aliases": [],
                "attributes": {"region": "Ohio"},
                "related_to_entity_indexes": [],
            },
            {
                "kind": "object",
                "name": "Red truck",
                "generation_prompt": "Old red truck under porch light.",
                "description": "His pickup truck.",
                "aliases": [],
                "attributes": {},
                "related_to_entity_indexes": [],
            },
        ],
        "traits": [
            {
                "name": "warmth",
                "description": "Welcoming and generous.",
            }
        ],
        "dropped_references": [
            {
                "dropped_phrase": "Aunt Mavis",
                "question_text": "Who was Aunt Mavis?",
                "themes": ["family"],
            }
        ],
        "extraction_notes": "Two anchored moments; one trait emerged.",
    }


def empty_extraction() -> dict[str, Any]:
    return {
        "moments": [],
        "entities": [],
        "traits": [],
        "dropped_references": [],
        "extraction_notes": "Segment too thin to anchor a moment.",
    }


def extraction_with_subject_self_reference() -> dict[str, Any]:
    """Extraction whose first entity collides with the legacy subject."""
    return {
        "moments": [
            {
                "title": "Talked about himself",
                "narrative": "He used to talk about himself in the third person.",
                "generation_prompt": "A figure waving by a fence.",
                "involves_entity_indexes": [0, 1],
            }
        ],
        "entities": [
            {
                "kind": "person",
                "name": "Test Subject",  # collides with persons.name
                "generation_prompt": "A figure by a fence.",
                "description": "The subject (oops).",
                "aliases": [],
                "attributes": {"relationship": "self"},
                "related_to_entity_indexes": [],
            },
            {
                "kind": "place",
                "name": "Old farmhouse",
                "generation_prompt": "A weathered farmhouse on a hill.",
                "description": "The childhood home.",
                "aliases": [],
                "attributes": {},
                "related_to_entity_indexes": [],
            },
        ],
        "traits": [],
        "dropped_references": [],
        "extraction_notes": "Subject self-referenced.",
    }
