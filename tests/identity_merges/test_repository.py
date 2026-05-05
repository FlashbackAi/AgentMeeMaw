from __future__ import annotations

import pytest
import pytest_asyncio

from flashback.db.connection import make_async_pool
from flashback.identity_merges.repository import approve_merge_async


@pytest_asyncio.fixture
async def async_pool(schema_applied: str):
    pool = make_async_pool(schema_applied, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def test_approve_merge_repoints_edges_marks_source_and_requeues_embedding(
    async_pool,
):
    sent: list[dict] = []

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Test Subject') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                await cur.execute(
                    """
                    INSERT INTO entities
                          (person_id, kind, name, description, aliases)
                    VALUES
                          (%s, 'person', 'Earlier label',
                           'Initially identified by an earlier label.', '{}'),
                          (%s, 'person', 'Person B',
                           'A close mutual friend.', '{}')
                    RETURNING id::text
                    """,
                    (person_id, person_id),
                )
                source_id, target_id = [row[0] for row in await cur.fetchall()]
                await cur.execute(
                    """
                    INSERT INTO moments (person_id, title, narrative)
                    VALUES (%s, 'Farmhouse', 'They were at the farmhouse.')
                    RETURNING id::text
                    """,
                    (person_id,),
                )
                moment_id = (await cur.fetchone())[0]
                await cur.execute(
                    """
                    INSERT INTO edges
                          (from_kind, from_id, to_kind, to_id, edge_type)
                    VALUES ('moment', %s, 'entity', %s, 'involves')
                    """,
                    (moment_id, source_id),
                )
                await cur.execute(
                    """
                    INSERT INTO identity_merge_suggestions
                          (person_id, source_entity_id, target_entity_id,
                           proposed_alias, reason)
                    VALUES (%s, %s, %s, 'Earlier label',
                            'User clarified the earlier label means Person B.')
                    RETURNING id::text
                    """,
                    (person_id, source_id, target_id),
                )
                suggestion_id = (await cur.fetchone())[0]

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await approve_merge_async(
                    cur,
                    suggestion_id=suggestion_id,
                    push_embedding=lambda **kwargs: sent.append(kwargs) or "msg-1",
                    embedding_model="voyage-3",
                    embedding_model_version="v1",
                )

    assert result is not None
    assert result.status == "approved"

    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, merged_into::text FROM entities WHERE id = %s",
                (source_id,),
            )
            assert await cur.fetchone() == ("merged", target_id)
            await cur.execute(
                """
                SELECT aliases, description_embedding, embedding_model,
                       embedding_model_version
                  FROM entities
                 WHERE id = %s
                """,
                (target_id,),
            )
            aliases, vector, model, version = await cur.fetchone()
            assert "Earlier label" in aliases
            assert vector is None
            assert model is None
            assert version is None
            await cur.execute(
                """
                SELECT to_id::text
                  FROM edges
                 WHERE from_kind = 'moment'
                   AND from_id = %s
                   AND to_kind = 'entity'
                """,
                (moment_id,),
            )
            assert (await cur.fetchone())[0] == target_id

    assert sent == [
        {
            "record_type": "entity",
            "record_id": target_id,
            "source_text": (
                "A close mutual friend. Also known from earlier context as: "
                "Initially identified by an earlier label."
            ),
            "embedding_model": "voyage-3",
            "embedding_model_version": "v1",
        }
    ]
