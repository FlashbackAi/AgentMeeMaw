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


async def _same_identity_medium(_candidate):
    return IdentityMergeVerification(
        verdict="same_identity",
        confidence="medium",
        reasoning="Probably the same person, but worth confirming.",
    )


async def _never_called(_candidate):  # pragma: no cover - asserts gate excludes
    raise AssertionError("verifier should not be called when no candidate forms")


async def _make_person(cur, name="Subject"):
    await cur.execute(
        "INSERT INTO persons (name) VALUES (%s) RETURNING id::text", (name,)
    )
    return (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_cross_kind_same_name_never_forms_a_candidate(async_pool):
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                person_id = await _make_person(cur)
                await cur.execute(
                    """
                    INSERT INTO entities (person_id, kind, name, description, aliases)
                    VALUES (%s, 'object', 'Comet', 'A bicycle.', '{}'),
                           (%s, 'place',  'Comet', 'A diner.',   '{}')
                    """,
                    (person_id, person_id),
                )

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await scan_identity_merge_suggestions_async(
                    cur, person_id=person_id, verifier=_never_called
                )
    assert result.candidates_considered == 0
    assert result.suggestions_created == 0
    assert result.auto_merged_count == 0


@pytest.mark.asyncio
async def test_name_in_description_cooccurrence_never_forms_a_candidate(async_pool):
    """'Mokshith' appearing in 'Mokshith's mother' is co-occurrence, not
    identity evidence — the deleted substring rule must stay dead."""
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                person_id = await _make_person(cur)
                await cur.execute(
                    """
                    INSERT INTO entities (person_id, kind, name, description, aliases)
                    VALUES (%s, 'person', 'Mokshith', 'The contributor.', '{}'),
                           (%s, 'person', 'Mokshith''s mother',
                            'Mokshith and his mother at home.', '{}')
                    """,
                    (person_id, person_id),
                )

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await scan_identity_merge_suggestions_async(
                    cur, person_id=person_id, verifier=_never_called
                )
    assert result.candidates_considered == 0


@pytest.mark.asyncio
async def test_same_name_high_confidence_auto_merges(async_pool):
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                person_id = await _make_person(cur)
                await cur.execute(
                    """
                    INSERT INTO entities (person_id, kind, name, description, aliases)
                    VALUES (%s, 'person', 'Ishita', 'One mention.', '{}'),
                           (%s, 'person', 'Ishita', 'Another mention.', '{}')
                    RETURNING id::text
                    """,
                    (person_id, person_id),
                )

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await scan_identity_merge_suggestions_async(
                    cur, person_id=person_id, verifier=_same_identity
                )

    assert result.candidates_considered == 1
    assert result.auto_merged_count == 1
    assert result.suggestions_created == 0

    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT count(*) FROM entities
                 WHERE person_id = %s AND status = 'active' AND name = 'Ishita'
                """,
                (person_id,),
            )
            assert (await cur.fetchone())[0] == 1
            await cur.execute(
                """
                SELECT status, notification_text, undo_snapshot IS NOT NULL
                  FROM identity_merge_suggestions WHERE person_id = %s
                """,
                (person_id,),
            )
            row = await cur.fetchone()
            assert row[0] == "auto_merged"
            assert row[1]  # LLM-authored notification text present
            assert row[2] is True  # undo snapshot captured


@pytest.mark.asyncio
async def test_same_name_medium_confidence_asks(async_pool):
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                person_id = await _make_person(cur)
                await cur.execute(
                    """
                    INSERT INTO entities (person_id, kind, name, description, aliases)
                    VALUES (%s, 'person', 'Mara', 'One.', '{}'),
                           (%s, 'person', 'Mara', 'Two.', '{}')
                    """,
                    (person_id, person_id),
                )

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await scan_identity_merge_suggestions_async(
                    cur, person_id=person_id, verifier=_same_identity_medium
                )

    assert result.auto_merged_count == 0
    assert result.suggestions_created == 1

    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT status, confidence, count(*) OVER ()
                  FROM identity_merge_suggestions WHERE person_id = %s
                """,
                (person_id,),
            )
            row = await cur.fetchone()
            assert row[0] == "pending"
            assert row[1] == "medium"
            # both entities remain active (no merge happened)
            await cur.execute(
                "SELECT count(*) FROM entities WHERE person_id = %s AND status='active'",
                (person_id,),
            )
            assert (await cur.fetchone())[0] == 2


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
