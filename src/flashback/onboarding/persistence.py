"""Persistence helpers for archetype onboarding."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Json

from flashback.onboarding.archetypes import COVERAGE_DIMENSIONS, sanitize_implies


@dataclass(frozen=True)
class PersonOnboardingRow:
    person_id: UUID
    relationship: str | None
    gender: str | None
    onboarding_complete: bool
    archetype_answers: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class EntityEmbeddingJob:
    entity_id: str
    source_text: str


@dataclass(frozen=True)
class OnboardingPersistResult:
    session_id: UUID
    embedding_jobs: list[EntityEmbeddingJob]
    coverage_deltas: dict[str, int]


async def fetch_person_onboarding(
    cur, *, person_id: UUID, for_update: bool = False
) -> PersonOnboardingRow | None:
    lock = " FOR UPDATE" if for_update else ""
    await cur.execute(
        f"""
        SELECT id,
               relationship,
               gender,
               COALESCE(onboarding_complete, false) AS onboarding_complete,
               COALESCE(archetype_answers, '[]'::jsonb) AS archetype_answers
          FROM persons
         WHERE id = %s
        {lock}
        """,
        (str(person_id),),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    returned_person_id, relationship, gender, complete, answers = row
    return PersonOnboardingRow(
        person_id=UUID(str(returned_person_id)),
        relationship=relationship,
        gender=gender,
        onboarding_complete=bool(complete),
        archetype_answers=list(answers or []),
    )


async def persist_archetype_onboarding(
    cur,
    *,
    person: PersonOnboardingRow,
    answers: list[dict[str, Any]],
    implies_blocks: list[dict[str, Any]],
) -> OnboardingPersistResult:
    """Persist onboarding answers and implied coverage state.

    Caller owns the transaction and has locked ``persons``.

    Onboarding no longer seeds entities. The free-text parser's "implied
    entities" bypassed extraction's quality bar (under-extract, subject
    guard, behavioral anchoring), which minted vague non-entities like
    "mutual friends" / "Friend" as persons — and those rows never got an
    artifact job, so they rendered as blank cards. Onboarding answers are
    ephemeral priors (cf. invariant #22d); extraction mines the resulting
    conversation for real entities. We keep the coverage deltas — they seed
    cold-start dimensions off ``implies["coverage"]``, independent of any
    entity. ``embedding_jobs`` is consequently always empty.
    """

    embedding_jobs: list[EntityEmbeddingJob] = []
    coverage_deltas = _coverage_deltas(implies_blocks)

    if any(coverage_deltas.values()):
        await _apply_coverage_deltas(
            cur, person_id=str(person.person_id), deltas=coverage_deltas
        )

    await cur.execute(
        """
        UPDATE persons
           SET archetype_answers = %s,
               onboarding_complete = true
         WHERE id = %s
        """,
        (Json(answers), str(person.person_id)),
    )

    return OnboardingPersistResult(
        session_id=uuid4(),
        embedding_jobs=embedding_jobs,
        coverage_deltas=coverage_deltas,
    )


def _coverage_deltas(implies_blocks: list[dict[str, Any]]) -> dict[str, int]:
    deltas = {dimension: 0 for dimension in COVERAGE_DIMENSIONS}
    for raw in implies_blocks:
        implies = sanitize_implies(raw)
        for dimension in set(implies.get("coverage", [])):
            if dimension in deltas:
                deltas[dimension] += 1
    return deltas


async def _apply_coverage_deltas(
    cur, *, person_id: str, deltas: dict[str, int]
) -> None:
    await cur.execute(
        """
        UPDATE persons
           SET coverage_state = jsonb_build_object(
                 'sensory',  COALESCE((coverage_state->>'sensory')::int, 0)
                             + %(sensory)s,
                 'voice',    COALESCE((coverage_state->>'voice')::int, 0)
                             + %(voice)s,
                 'place',    COALESCE((coverage_state->>'place')::int, 0)
                             + %(place)s,
                 'relation', COALESCE((coverage_state->>'relation')::int, 0)
                             + %(relation)s,
                 'era',      COALESCE((coverage_state->>'era')::int, 0)
                             + %(era)s
               )
         WHERE id = %(person_id)s
        """,
        {
            **{dimension: deltas.get(dimension, 0) for dimension in COVERAGE_DIMENSIONS},
            "person_id": person_id,
        },
    )
