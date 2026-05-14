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
    """Persist onboarding answers and implied graph state.

    Caller owns the transaction and has locked ``persons``.
    """

    embedding_jobs: list[EntityEmbeddingJob] = []
    coverage_deltas = _coverage_deltas(implies_blocks)

    for answer, raw_implies in zip(answers, implies_blocks, strict=True):
        implies = sanitize_implies(raw_implies)
        for raw_entity in implies.get("entities", []):
            job = await _upsert_entity(
                cur,
                person_id=str(person.person_id),
                entity=raw_entity,
                answer=answer,
                relationship=person.relationship,
            )
            if job is not None:
                embedding_jobs.append(job)

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


async def _upsert_entity(
    cur,
    *,
    person_id: str,
    entity: dict[str, Any],
    answer: dict[str, Any],
    relationship: str | None,
) -> EntityEmbeddingJob | None:
    kind = str(entity.get("type") or entity.get("kind") or "").strip().lower()
    name = str(entity.get("name") or "").strip()
    if not kind or not name:
        return None
    description = str(entity.get("description") or "").strip()
    if not description:
        rel = f" connected to the contributor's {relationship}" if relationship else ""
        description = f"{name} was mentioned during onboarding{rel}."

    attributes = dict(entity.get("attributes") or {})
    attributes["source"] = "archetype_onboarding"
    attributes["question_id"] = answer.get("question_id")
    if answer.get("option_id"):
        attributes["option_id"] = answer.get("option_id")

    await cur.execute(
        """
        SELECT id::text
          FROM active_entities
         WHERE person_id = %s
           AND kind = %s
           AND lower(name) = lower(%s)
         LIMIT 1
        """,
        (person_id, kind, name),
    )
    row = await cur.fetchone()
    if row is not None:
        entity_id = str(row[0])
        await cur.execute(
            """
            UPDATE entities
               SET description = COALESCE(description, %s),
                   attributes = attributes || %s::jsonb
             WHERE id = %s
               AND person_id = %s
               AND status = 'active'
            """,
            (description, Json(attributes), entity_id, person_id),
        )
        return None

    aliases = [
        str(alias).strip()
        for alias in entity.get("aliases", []) or []
        if str(alias).strip()
    ]
    await cur.execute(
        """
        INSERT INTO entities (person_id, kind, name, description, aliases, attributes)
        VALUES               (%s,        %s,   %s,   %s,          %s,      %s)
        RETURNING id::text
        """,
        (person_id, kind, name, description, aliases, Json(attributes)),
    )
    entity_id = str((await cur.fetchone())[0])
    return EntityEmbeddingJob(entity_id=entity_id, source_text=description)


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
