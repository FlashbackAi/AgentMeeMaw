"""Persons-table writes used by ``POST /persons``.

The schema (migration 0001 plus 0009) gives every other column a
sensible default — phase defaults to ``'starter'``, coverage_state to
the all-zero anchor map, ``moments_at_last_thread_run`` to 0, and the
artifact URL/prompt columns to NULL. So creation is a single
``INSERT ... RETURNING`` over ``(name, relationship)``; the database
fills in the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from flashback.themes.repository import (
    ensure_tribute_theme_async,
    seed_universal_themes_async,
)
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)

_INSERT_PERSON = """
INSERT INTO persons (name, relationship, gender, contributor_gender)
VALUES (%(name)s, %(relationship)s, %(gender)s, %(contributor_gender)s)
RETURNING id, name, relationship, gender, contributor_gender, phase, created_at
"""

_SELECT_PERSON_BY_ID = """
SELECT id, name, relationship, gender, contributor_gender, phase
FROM persons
WHERE id = %s
"""


@dataclass(frozen=True)
class PersonProfile:
    person_id: UUID
    name: str
    relationship: str
    gender: str | None
    contributor_gender: str | None
    phase: str


@dataclass(frozen=True)
class CreatedPerson:
    person_id: UUID
    name: str
    relationship: str
    gender: str | None
    contributor_gender: str | None
    phase: str
    created_at: datetime


async def insert_person(
    db_pool: AsyncConnectionPool,
    *,
    name: str,
    relationship: str,
    gender: str | None = None,
    contributor_gender: str | None = None,
) -> CreatedPerson:
    """Insert one ``persons`` row and return the persisted shape.

    The 5 universal themes + the on-demand-style tribute theme are seeded
    in the same transaction so a legacy is never observably created
    without its theme grid, and the tribute theme is discoverable via the
    standard unlock sequence (active_themes_with_tier -> unlock_prepare ->
    session/start) with no special endpoint.
    """
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    _INSERT_PERSON,
                    {
                        "name": name,
                        "relationship": relationship,
                        "gender": gender,
                        "contributor_gender": contributor_gender,
                    },
                )
                row = await cur.fetchone()
                assert row is not None  # INSERT ... RETURNING always yields a row
                await seed_universal_themes_async(cur, person_id=row[0])
                await ensure_tribute_theme_async(
                    cur,
                    person_id=row[0],
                    slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION,
                )

    (
        person_id,
        returned_name,
        returned_relationship,
        returned_gender,
        returned_contributor_gender,
        phase,
        created_at,
    ) = row
    return CreatedPerson(
        person_id=person_id,
        name=returned_name,
        relationship=returned_relationship,
        gender=returned_gender,
        contributor_gender=returned_contributor_gender,
        phase=phase,
        created_at=created_at,
    )


async def get_person_by_id(
    db_pool: AsyncConnectionPool,
    *,
    person_id: UUID,
) -> PersonProfile | None:
    """Fetch a minimal persons row by id. Returns None if not found or inactive."""
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_SELECT_PERSON_BY_ID, (str(person_id),))
            row = await cur.fetchone()
    if row is None:
        return None
    pid, name, relationship, gender, contributor_gender, phase = row
    return PersonProfile(
        person_id=pid,
        name=name,
        relationship=relationship,
        gender=gender,
        contributor_gender=contributor_gender,
        phase=phase,
    )
