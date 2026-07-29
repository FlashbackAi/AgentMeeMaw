"""Auto-merge → unmerge round-trip.

The merged-away entity is resurrected as a fresh standalone entity and
its edges move back to it. The survivor keeps its blended description,
but the labels the merge folded into its aliases are stripped and the
suggestion row is repointed to the resurrected id — otherwise the next
scan re-detects the exact pair the user just pulled apart and silently
re-auto-merges it (the ping-pong observed in prod, 2026-06-17→19).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from flashback.db.connection import make_async_pool
from flashback.identity_merges.repository import auto_merge_async, unmerge_async
from flashback.identity_merges.scanner import _find_candidates


@pytest_asyncio.fixture
async def async_pool(schema_applied: str):
    pool = make_async_pool(schema_applied, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _setup(cur):
    await cur.execute(
        "INSERT INTO persons (name) VALUES ('Subject') RETURNING id::text"
    )
    person_id = (await cur.fetchone())[0]
    await cur.execute(
        """
        INSERT INTO entities (person_id, kind, name, description, aliases)
        VALUES (%s, 'person', 'Aarav', 'Source row.', '{}')
        RETURNING id::text
        """,
        (person_id,),
    )
    source_id = (await cur.fetchone())[0]
    await cur.execute(
        """
        INSERT INTO entities (person_id, kind, name, description, aliases)
        VALUES (%s, 'person', 'Aarav', 'Survivor row.', '{}')
        RETURNING id::text
        """,
        (person_id,),
    )
    target_id = (await cur.fetchone())[0]
    await cur.execute(
        """
        INSERT INTO moments (person_id, title, narrative)
        VALUES (%s, 'A day out', 'We spent the day together.')
        RETURNING id::text
        """,
        (person_id,),
    )
    moment_id = (await cur.fetchone())[0]
    await cur.execute(
        """
        INSERT INTO edges (from_kind, from_id, to_kind, to_id, edge_type)
        VALUES ('moment', %s, 'entity', %s, 'involves')
        """,
        (moment_id, source_id),
    )
    return person_id, source_id, target_id, moment_id


async def _involves_target(cur, moment_id):
    await cur.execute(
        """
        SELECT to_id::text FROM edges
         WHERE from_kind='moment' AND from_id=%s AND edge_type='involves'
        """,
        (moment_id,),
    )
    rows = await cur.fetchall()
    return [r[0] for r in rows]


@pytest.mark.asyncio
async def test_auto_merge_then_unmerge_round_trip(async_pool):
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                person_id, source_id, target_id, moment_id = await _setup(cur)

    # Auto-merge source -> target.
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                suggestion_id = await auto_merge_async(
                    cur,
                    person_id=person_id,
                    source_id=source_id,
                    target_id=target_id,
                    proposed_alias="Aarav",
                    confidence="high",
                    notification_text="Combined two Aarav entries.",
                    push_embedding=None,
                    embedding_model="voyage-test",
                    embedding_model_version="v1",
                )
    assert suggestion_id is not None

    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            # Edge repointed to survivor; source is a merged tombstone.
            assert await _involves_target(cur, moment_id) == [target_id]
            await cur.execute(
                "SELECT status, merged_into::text FROM entities WHERE id=%s",
                (source_id,),
            )
            assert await cur.fetchone() == ("merged", target_id)

    # Unmerge.
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await unmerge_async(
                    cur,
                    suggestion_id=suggestion_id,
                    push_embedding=None,
                    embedding_model="voyage-test",
                    embedding_model_version="v1",
                )
    assert result is not None
    resurrected = str(result.resurrected_entity_id)
    assert resurrected not in (source_id, target_id)

    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            # Edge moved back to the resurrected entity; survivor no longer
            # carries it.
            assert await _involves_target(cur, moment_id) == [resurrected]
            # Resurrected entity is active with the original name/kind.
            await cur.execute(
                "SELECT status, kind, name FROM entities WHERE id=%s",
                (resurrected,),
            )
            assert await cur.fetchone() == ("active", "person", "Aarav")
            # Survivor stays intact and active.
            await cur.execute(
                "SELECT status FROM entities WHERE id=%s", (target_id,)
            )
            assert (await cur.fetchone())[0] == "active"
            # Suggestion marked unmerged.
            await cur.execute(
                "SELECT status FROM identity_merge_suggestions WHERE id=%s",
                (suggestion_id,),
            )
            assert (await cur.fetchone())[0] == "unmerged"


async def _merge_then_unmerge(async_pool, *, person_id, source_id, target_id, proposed_alias):
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                suggestion_id = await auto_merge_async(
                    cur,
                    person_id=person_id,
                    source_id=source_id,
                    target_id=target_id,
                    proposed_alias=proposed_alias,
                    confidence="high",
                    notification_text="Combined two entries.",
                    push_embedding=None,
                    embedding_model="voyage-test",
                    embedding_model_version="v1",
                )
    assert suggestion_id is not None
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await unmerge_async(
                    cur,
                    suggestion_id=suggestion_id,
                    push_embedding=None,
                    embedding_model="voyage-test",
                    embedding_model_version="v1",
                )
    assert result is not None
    return suggestion_id, str(result.resurrected_entity_id)


@pytest.mark.asyncio
async def test_unmerge_strips_folded_aliases_from_survivor(async_pool):
    """The merge folds the source's labels into the survivor's aliases;
    unmerge must take them back out, or the alias evidence re-pairs the
    two entities on the very next scan."""
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Subject') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                await cur.execute(
                    """
                    INSERT INTO entities (person_id, kind, name, description, aliases)
                    VALUES (%s, 'person', 'Sangeetha', 'The friend.', '{Sangi,Srija}')
                    RETURNING id::text
                    """,
                    (person_id,),
                )
                source_id = (await cur.fetchone())[0]
                await cur.execute(
                    """
                    INSERT INTO entities (person_id, kind, name, description, aliases)
                    VALUES (%s, 'person', 'Srija', 'College friend.', '{Sri,Sangeetha}')
                    RETURNING id::text
                    """,
                    (person_id,),
                )
                target_id = (await cur.fetchone())[0]

    _sid, resurrected = await _merge_then_unmerge(
        async_pool,
        person_id=person_id,
        source_id=source_id,
        target_id=target_id,
        proposed_alias="Sangeetha",
    )

    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            # Survivor: every label of the pulled-apart identity is gone
            # ('Sangeetha' and 'Sangi'); its own unrelated alias survives.
            await cur.execute(
                "SELECT aliases FROM entities WHERE id=%s", (target_id,)
            )
            assert (await cur.fetchone())[0] == ["Sri"]
            # Resurrected entity: keeps its own aliases minus the
            # survivor's name (the mirror-direction evidence).
            await cur.execute(
                "SELECT aliases FROM entities WHERE id=%s", (resurrected,)
            )
            assert (await cur.fetchone())[0] == ["Sangi"]


@pytest.mark.asyncio
async def test_unmerge_suppresses_rescan_of_the_pair(async_pool):
    """Same-name pairs can't be fixed by alias stripping — the suggestion
    row must follow the resurrected id so the scanner's dedup gate keeps
    blocking the pair."""
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Subject') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                await cur.execute(
                    """
                    INSERT INTO entities (person_id, kind, name, description, aliases)
                    VALUES (%s, 'place', 'Codeunnati Lab', 'The lab.', '{}')
                    RETURNING id::text
                    """,
                    (person_id,),
                )
                source_id = (await cur.fetchone())[0]
                await cur.execute(
                    """
                    INSERT INTO entities (person_id, kind, name, description, aliases)
                    VALUES (%s, 'place', 'Codeunnati lab', 'Where they taught.', '{}')
                    RETURNING id::text
                    """,
                    (person_id,),
                )
                target_id = (await cur.fetchone())[0]

    suggestion_id, resurrected = await _merge_then_unmerge(
        async_pool,
        person_id=person_id,
        source_id=source_id,
        target_id=target_id,
        proposed_alias="Codeunnati Lab",
    )

    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            # The tombstone now references the live pair, with the original
            # id preserved for audit.
            await cur.execute(
                """
                SELECT source_entity_id::text, undo_snapshot
                  FROM identity_merge_suggestions WHERE id=%s
                """,
                (suggestion_id,),
            )
            row = await cur.fetchone()
            assert row[0] == resurrected
            assert row[1]["original_source_entity_id"] == source_id
            # And the scanner no longer proposes the pair the user just
            # pulled apart.
            candidates = await _find_candidates(
                cur, person_id=person_id, limit=20
            )
            assert candidates == []
