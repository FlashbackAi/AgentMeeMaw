"""collaborator_onboarding repository (DB-gated)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from flashback.collaborator_onboarding import get_voice_anchor, upsert_onboarding
from flashback.db.connection import make_async_pool

_DB = os.environ.get("TEST_DATABASE_URL")


async def _person(pool) -> str:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO persons (name) VALUES (%s) RETURNING id", ("Subj",)
        )
        pid = (await cur.fetchone())[0]
        await conn.commit()
    return pid


@pytest_asyncio.fixture
async def pool(schema_applied: str):
    p = make_async_pool(schema_applied, min_size=1, max_size=2)
    await p.open()
    try:
        yield p
    finally:
        async with p.connection() as conn:
            await conn.execute("DELETE FROM collaborator_onboarding")
            await conn.execute("DELETE FROM persons")
            await conn.commit()
        await p.close()


async def test_upsert_creates_then_reads_voice_anchor(pool):
    pid = await _person(pool)
    uid = uuid4()
    now = datetime(2026, 6, 16, tzinfo=timezone.utc)
    async with pool.connection() as conn:
        await upsert_onboarding(
            conn, person_id=pid, user_id=uid,
            voice_anchor_text="his daughter", voice_anchored_at=now,
            modal_answered_at=now,
        )
        await conn.commit()
        assert await get_voice_anchor(conn, person_id=pid, user_id=uid) == "his daughter"


async def test_reupsert_does_not_clobber_anchor_with_null(pool):
    pid = await _person(pool)
    uid = uuid4()
    now = datetime(2026, 6, 16, tzinfo=timezone.utc)
    async with pool.connection() as conn:
        await upsert_onboarding(
            conn, person_id=pid, user_id=uid,
            voice_anchor_text="his daughter", voice_anchored_at=now,
        )
        await conn.commit()
        # Re-mirror with no anchor (e.g. a later session without the modal)
        await upsert_onboarding(
            conn, person_id=pid, user_id=uid, modal_dismissed_at=now,
        )
        await conn.commit()
        assert await get_voice_anchor(conn, person_id=pid, user_id=uid) == "his daughter"


async def test_get_voice_anchor_none_when_absent(pool):
    pid = await _person(pool)
    async with pool.connection() as conn:
        assert await get_voice_anchor(conn, person_id=pid, user_id=uuid4()) is None
