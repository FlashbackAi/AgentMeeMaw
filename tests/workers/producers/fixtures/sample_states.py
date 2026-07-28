"""Small reusable producer test payloads."""

from __future__ import annotations


def p2_result(entity_id: str, subject_name: str = "the subject") -> dict:
    # P2 drops any question that is not subject-centered, so the text has to
    # name the subject (or use a pronoun) -- not just the target entity.
    return {
        "questions": [
            {
                "text": (
                    f"How did {subject_name} and Uncle Raj spend time "
                    "together at the shop?"
                ),
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

