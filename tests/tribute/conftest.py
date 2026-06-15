"""Async DB pool fixture for tribute tests (mirrors tests/identity_merges)."""

from __future__ import annotations

import pytest_asyncio

from flashback.db.connection import make_async_pool


@pytest_asyncio.fixture
async def async_pool(schema_applied: str):
    pool = make_async_pool(schema_applied, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()
