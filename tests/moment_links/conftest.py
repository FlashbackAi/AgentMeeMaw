from __future__ import annotations

import pytest_asyncio

from flashback.db.connection import make_async_pool


@pytest_asyncio.fixture
async def async_db_pool(schema_applied: str):
    """Async pool against a freshly-migrated test DB (migrations incl. 0033).

    Mirrors tests/retrieval/conftest.py; tears down SP5 + base rows so each
    test starts clean.
    """
    pool = make_async_pool(schema_applied, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM moment_same_event_links")
                await cur.execute("DELETE FROM moment_contradictions")
                await cur.execute("DELETE FROM edges")
                await cur.execute("DELETE FROM moment_history")
                await cur.execute("DELETE FROM moments")
                await cur.execute("DELETE FROM collaborator_onboarding")
                await cur.execute("DELETE FROM persons")
                await conn.commit()
        await pool.close()
