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

_INSERT_PERSON = """
INSERT INTO persons (name, relationship, gender)
VALUES (%(name)s, %(relationship)s, %(gender)s)
RETURNING id, name, relationship, gender, phase, created_at
"""


@dataclass(frozen=True)
class CreatedPerson:
    person_id: UUID
    name: str
    relationship: str
    gender: str | None
    phase: str
    created_at: datetime


async def insert_person(
    db_pool: AsyncConnectionPool,
    *,
    name: str,
    relationship: str,
    gender: str | None = None,
) -> CreatedPerson:
    """Insert one ``persons`` row and return the persisted shape."""
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    _INSERT_PERSON,
                    {"name": name, "relationship": relationship, "gender": gender},
                )
                row = await cur.fetchone()

    assert row is not None  # INSERT ... RETURNING always yields a row
    person_id, returned_name, returned_relationship, returned_gender, phase, created_at = row
    return CreatedPerson(
        person_id=person_id,
        name=returned_name,
        relationship=returned_relationship,
        gender=returned_gender,
        phase=phase,
        created_at=created_at,
    )
