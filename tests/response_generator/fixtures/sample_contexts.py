from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from flashback.response_generator.schema import (
    FirstTimeOpenerContext,
    StarterContext,
    Turn,
    TurnContext,
)
from flashback.retrieval.schema import EntityResult, MomentResult, ThreadResult

T0 = datetime(2026, 5, 4, tzinfo=timezone.utc)
PERSON_ID = UUID("11111111-1111-1111-1111-111111111111")
MOMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
ENTITY_ID = UUID("33333333-3333-3333-3333-333333333333")
THREAD_ID = UUID("44444444-4444-4444-4444-444444444444")


def sample_turn_context(intent: str = "story") -> TurnContext:
    return TurnContext(
        person_name="Maya",
        person_relationship="mother",
        intent=intent,
        emotional_temperature="medium",
        rolling_summary="Maya's kitchen and porch have come up before.",
        recent_turns=[
            Turn(role="assistant", content="What do you remember first?", timestamp=T0),
            Turn(role="user", content="The porch light was always on.", timestamp=T0),
        ],
        related_moments=[
            MomentResult(
                id=MOMENT_ID,
                person_id=PERSON_ID,
                title="Porch evenings",
                narrative="Maya sat on the porch after dinner.",
                time_anchor=None,
                life_period_estimate=None,
                sensory_details="warm light",
                emotional_tone="tender",
                contributor_perspective="adult child",
                created_at=T0,
                similarity_score=0.32,
            )
        ],
        related_entities=[
            EntityResult(
                id=ENTITY_ID,
                person_id=PERSON_ID,
                kind="place",
                name="Porch",
                description="The front porch at the family house.",
                aliases=[],
                attributes={},
                created_at=T0,
            )
        ],
        related_threads=[
            ThreadResult(
                id=THREAD_ID,
                person_id=PERSON_ID,
                name="Evening routines",
                description="Small rituals that made home feel steady.",
                source="auto-detected",
                confidence=0.8,
                created_at=T0,
            )
        ],
    )


def sample_starter_context() -> StarterContext:
    return StarterContext(
        person_name="Maya",
        person_relationship="mother",
        contributor_display_name="Sarah",
        contributor_role="adult child",
        anchor_question_text="What's a smell that brings them right back?",
        anchor_dimension="sensory",
        prior_session_summary=None,
    )


def sample_first_time_opener_context() -> FirstTimeOpenerContext:
    return FirstTimeOpenerContext(
        person_name="Maya",
        person_relationship="mother",
        contributor_display_name="Sarah",
        anchor_question_text="What's a smell that brings them right back?",
        anchor_dimension="sensory",
        archetype_answers=[
            {
                "question_id": "parent_early_scene",
                "option_id": "home",
                "label": "At home",
            }
        ],
    )
