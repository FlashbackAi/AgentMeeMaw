import os

import pytest

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
async def test_tables_exist(async_db_pool):
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT to_regclass('moment_same_event_links'), "
                "to_regclass('moment_contradictions')"
            )
            a, b = await cur.fetchone()
    assert a is not None
    assert b is not None


@db_only
async def test_distinct_check_rejects_self_pair(async_db_pool):
    """The CHECK (moment_a_id <> moment_b_id) constraint forbids self-pairs."""
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text"
            )
            (pid,) = await cur.fetchone()
            await cur.execute(
                "INSERT INTO moments (person_id, title, narrative, status) "
                "VALUES (%s, 'A', 'n', 'active') RETURNING id::text",
                (pid,),
            )
            (mid,) = await cur.fetchone()
            await conn.commit()
        async with conn.cursor() as cur:
            with pytest.raises(Exception):
                await cur.execute(
                    "INSERT INTO moment_same_event_links "
                    "(person_id, moment_a_id, moment_b_id) VALUES (%s, %s, %s)",
                    (pid, mid, mid),
                )
            await conn.rollback()
