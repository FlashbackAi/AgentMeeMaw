from __future__ import annotations

import pytest
import pytest_asyncio

from flashback.db.connection import make_async_pool
from flashback.identity_merges.scanner import scan_identity_merge_suggestions_async
from flashback.identity_merges.verifier import IdentityMergeVerification


@pytest_asyncio.fixture
async def async_pool(schema_applied: str):
    pool = make_async_pool(schema_applied, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _same_identity(_candidate):
    return IdentityMergeVerification(
        verdict="same_identity",
        confidence="high",
        reasoning="The candidate text identifies the labels as the same identity.",
    )


async def _unsure(_candidate):
    return IdentityMergeVerification(
        verdict="unsure",
        confidence="low",
        reasoning="The evidence could describe related but separate identities.",
    )


@pytest.mark.asyncio
async def test_scanner_creates_pending_suggestion_after_verifier_confirms(
    async_pool,
):
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Subject') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                await cur.execute(
                    """
                    INSERT INTO entities
                          (person_id, kind, name, description, aliases)
                    VALUES
                          (%s, 'person', 'Earlier label',
                           'A row created from an earlier phrase.', '{}'),
                          (%s, 'person', 'Canonical label',
                           'Canonical label is also known as Earlier label.', '{}')
                    """,
                    (person_id, person_id),
                )

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await scan_identity_merge_suggestions_async(
                    cur,
                    person_id=person_id,
                    verifier=_same_identity,
                )

    assert result.candidates_considered == 1
    assert result.verifier_calls == 1
    assert result.suggestions_created == 1

    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT src.name, tgt.name, s.proposed_alias, s.source, s.status
                  FROM identity_merge_suggestions s
                  JOIN entities src ON src.id = s.source_entity_id
                  JOIN entities tgt ON tgt.id = s.target_entity_id
                 WHERE s.person_id = %s
                """,
                (person_id,),
            )
            assert await cur.fetchone() == (
                "Earlier label",
                "Canonical label",
                "Earlier label",
                "scanner",
                "pending",
            )


@pytest.mark.asyncio
async def test_scanner_does_not_suggest_from_embedding_similarity_alone(async_pool):
    left_vector = [0.0] * 1024
    left_vector[0] = 1.0
    right_vector = [0.0] * 1024
    right_vector[0] = 0.999
    right_vector[1] = 0.001

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Subject') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                await cur.execute(
                    """
                    INSERT INTO entities
                          (person_id, kind, name, description, aliases,
                           description_embedding, embedding_model, embedding_model_version)
                    VALUES
                          (%s, 'person', 'First phrasing',
                           'Reserved training friend.', '{}',
                           %s, 'voyage-test', 'v1'),
                          (%s, 'person', 'Second phrasing',
                           'Quiet friend from the same training circle.', '{}',
                           %s, 'voyage-test', 'v1')
                    """,
                    (person_id, left_vector, person_id, right_vector),
                )

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await scan_identity_merge_suggestions_async(
                    cur,
                    person_id=person_id,
                    verifier=_same_identity,
                    embedding_distance_threshold=0.01,
                )

    assert result.candidates_considered == 0
    assert result.suggestions_created == 0


@pytest.mark.asyncio
async def test_scanner_does_not_write_when_verifier_is_unsure(async_pool):
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Subject') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                await cur.execute(
                    """
                    INSERT INTO entities
                          (person_id, kind, name, description, aliases)
                    VALUES
                          (%s, 'person', 'Duplicate name', 'One row.', '{}'),
                          (%s, 'person', 'Duplicate name', 'Another row.', '{}')
                    """,
                    (person_id, person_id),
                )

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await scan_identity_merge_suggestions_async(
                    cur,
                    person_id=person_id,
                    verifier=_unsure,
                )

    assert result.candidates_considered == 1
    assert result.suggestions_created == 0
