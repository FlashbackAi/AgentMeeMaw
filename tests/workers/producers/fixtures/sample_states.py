"""Small reusable producer test payloads."""

from __future__ import annotations


def p2_result(entity_id: str) -> dict:
    return {
        "questions": [
            {
                "text": "What did Uncle Raj do that made the kitchen feel alive?",
                "targets_entity_id": entity_id,
                "themes": ["family", "place"],
            }
        ],
        "overall_reasoning": "The entity is mentioned but thin.",
    }


def p3_result(life_period: str) -> dict:
    return {
        "questions": [
            {
                "text": f"What was changing in their world during the {life_period}?",
                "life_period": life_period,
                "themes": ["era"],
            }
        ],
        "overall_reasoning": "Missing period.",
    }


def p5_result(dimension: str) -> dict:
    return {
        "questions": [
            {
                "text": f"What do you remember about their {dimension}?",
                "dimension": dimension,
                "themes": [dimension],
            }
        ],
        "overall_reasoning": "Under-covered universal dimension.",
    }

