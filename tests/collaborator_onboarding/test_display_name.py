"""DB-gated test: display_name is mirrored and not clobbered on re-upsert."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest_asyncio

from flashback.collaborator_onboarding.repository import upsert_onboarding
from flashback.db.connection import make_async_pool


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


async def test_display_name_mirrored_and_not_clobbered(pool):
    pid = await _person(pool)
    uid = uuid4()
    now = datetime(2026, 6, 17, tzinfo=timezone.utc)

    # 1. Upsert with display_name="Keerthi" + a voice anchor.
    async with pool.connection() as conn:
        await upsert_onboarding(
            conn,
            person_id=pid,
            user_id=uid,
            voice_anchor_text="his daughter",
            voice_anchored_at=now,
            display_name="Keerthi",
        )
        await conn.commit()

        cur = await conn.execute(
            "SELECT display_name FROM collaborator_onboarding"
            " WHERE person_id = %s AND user_id = %s AND status = 'active'",
            (pid, uid),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "Keerthi"

    # 2. Re-upsert with display_name=None; value must stay "Keerthi" (COALESCE).
    async with pool.connection() as conn:
        await upsert_onboarding(
            conn,
            person_id=pid,
            user_id=uid,
            modal_dismissed_at=now,
            display_name=None,
        )
        await conn.commit()

        cur = await conn.execute(
            "SELECT display_name FROM collaborator_onboarding"
            " WHERE person_id = %s AND user_id = %s AND status = 'active'",
            (pid, uid),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "Keerthi", "display_name must not be clobbered by NULL re-upsert"
