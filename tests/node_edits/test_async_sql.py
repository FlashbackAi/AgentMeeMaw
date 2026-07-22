"""DB tests for the async prevention-layer-1 reuse path.

``insert_entities_async`` (used by node-edit refinement) is the second
deterministic-reuse path named by spec §4 alongside the sync
``_persist_entities`` / ``_reuse_existing_entity`` in
:mod:`flashback.workers.extraction.persistence`. Both must fold a
newly-known ``attributes.gender`` into an existing entity only when the
stored value is empty (invariant #17a) -- never overwrite a confident
value with a later ambiguous one.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from psycopg.types.json import Json

from flashback.db.connection import make_async_pool
from flashback.node_edits._async_sql import insert_entities_async
from flashback.workers.extraction.schema import ExtractedEntity


@pytest_asyncio.fixture
async def async_pool(schema_applied: str):
    pool = make_async_pool(schema_applied, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _make_person(pool, name: str = "Test Subject") -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES (%s) RETURNING id::text",
                    (name,),
                )
                return (await cur.fetchone())[0]


async def _insert_entity(
    pool, person_id: str, name: str, *, attributes: dict | None = None
) -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO entities
                          (person_id, kind, name, description, aliases, attributes)
                    VALUES (%s, 'person', %s, 'An existing person.', '{}', %s)
                    RETURNING id::text
                    """,
                    (person_id, name, Json(attributes or {})),
                )
                return (await cur.fetchone())[0]


async def _get_attributes(pool, entity_id: str) -> dict:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT attributes FROM entities WHERE id = %s", (entity_id,)
            )
            (attributes,) = await cur.fetchone()
    return attributes or {}


def _extracted_entity(name: str, *, gender: str | None = None) -> ExtractedEntity:
    attributes: dict = {"relationship": "friend"}
    if gender is not None:
        attributes["gender"] = gender
    return ExtractedEntity(
        kind="person",
        name=name,
        generation_prompt="A friend at a farmhouse party.",
        description="A close friend.",
        aliases=[],
        attributes=attributes,
        related_to_entity_indexes=[],
    )


async def test_reuse_fills_empty_gender_from_incoming_attributes(async_pool):
    """Deterministic reuse (invariant #17a), second path: a newly-known
    ``attributes.gender`` folds into an existing entity via
    ``insert_entities_async`` only when the stored value is empty."""
    person_id = await _make_person(async_pool)
    existing_id = await _insert_entity(async_pool, person_id, "Aarav")

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                results = await insert_entities_async(
                    cur,
                    person_id=person_id,
                    entities=[_extracted_entity("Aarav", gender="male")],
                    llm_provenance=None,
                )

    assert len(results) == 1
    assert results[0].id == existing_id
    assert results[0].reused is True
    assert (await _get_attributes(async_pool, existing_id)).get("gender") == "male"


async def test_reuse_never_overwrites_an_already_set_gender(async_pool):
    """A later mention with a different/ambiguous gender must never clobber
    a confidently-stored one, via ``insert_entities_async`` too."""
    person_id = await _make_person(async_pool)
    existing_id = await _insert_entity(
        async_pool, person_id, "Aarav", attributes={"gender": "male"}
    )

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                results = await insert_entities_async(
                    cur,
                    person_id=person_id,
                    entities=[_extracted_entity("Aarav", gender="female")],
                    llm_provenance=None,
                )

    assert len(results) == 1
    assert results[0].id == existing_id
    assert (await _get_attributes(async_pool, existing_id)).get("gender") == "male"
