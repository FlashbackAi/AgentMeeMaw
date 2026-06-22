"""DB-gated test: OnboardingState helpers and phase-flip logic."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from flashback.collaborator_onboarding.repository import (
    OnboardingState,
    get_onboarding_state,
    flip_phase_if_complete,
    increment_taps_emitted,
    upsert_onboarding,
)
from flashback.collaborator_onboarding.queries import MARK_FIRST_MOMENT_SQL
from flashback.db.connection import make_async_pool

pytestmark = pytest.mark.asyncio


async def _person(pool) -> str:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO persons (name) VALUES (%s) RETURNING id", ("Subj",)
        )
        pid = (await cur.fetchone())[0]
        await conn.commit()
    return pid


async def _insert_moment(conn, person_id, user_id):
    mid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO moments (id, person_id, title, narrative, told_by_user_id)"
        " VALUES (%s,%s,'m','n',%s)",
        (str(mid), str(person_id), str(user_id)),
    )
    return mid


@pytest_asyncio.fixture
async def pool(schema_applied: str):
    p = make_async_pool(schema_applied, min_size=1, max_size=2)
    await p.open()
    try:
        yield p
    finally:
        async with p.connection() as conn:
            await conn.execute("DELETE FROM collaborator_onboarding")
            await conn.execute("DELETE FROM moments")
            await conn.execute("DELETE FROM persons")
            await conn.commit()
        await p.close()


async def test_get_state_none_when_no_row(pool):
    pid = await _person(pool)
    async with pool.connection() as conn:
        assert await get_onboarding_state(conn, person_id=pid, user_id=uuid.uuid4()) is None


async def test_flip_requires_both_items(pool):
    pid = await _person(pool)
    user_id = uuid.uuid4()

    # Insert row with voice anchor only (has_connection=True, has_memory=False).
    async with pool.connection() as conn:
        await upsert_onboarding(
            conn, person_id=pid, user_id=user_id,
            voice_anchor_text="his daughter",
            voice_anchored_at=datetime.now(timezone.utc),
        )
        await conn.commit()

    # flip_phase_if_complete should NOT advance — no memory yet.
    async with pool.connection() as conn:
        await flip_phase_if_complete(conn, person_id=pid, user_id=user_id)
        await conn.commit()

    async with pool.connection() as conn:
        st = await get_onboarding_state(conn, person_id=pid, user_id=user_id)
    assert st is not None
    assert st.phase == "onboarding"
    assert st.has_connection is True
    assert st.has_memory is False

    # Now stamp a first_moment via MARK_FIRST_MOMENT_SQL.
    async with pool.connection() as conn:
        mid = await _insert_moment(conn, pid, user_id)
        await conn.commit()

    async with pool.connection() as conn:
        await conn.execute(
            MARK_FIRST_MOMENT_SQL,
            {"person_id": str(pid), "user_id": str(user_id), "moment_id": str(mid)},
        )
        await conn.commit()

    # Now both items satisfied — flip should advance to 'active'.
    async with pool.connection() as conn:
        await flip_phase_if_complete(conn, person_id=pid, user_id=user_id)
        await conn.commit()

    async with pool.connection() as conn:
        st = await get_onboarding_state(conn, person_id=pid, user_id=user_id)
    assert st is not None
    assert st.phase == "active"


async def test_increment_taps(pool):
    pid = await _person(pool)
    user_id = uuid.uuid4()

    async with pool.connection() as conn:
        await upsert_onboarding(conn, person_id=pid, user_id=user_id)
        await conn.commit()

    async with pool.connection() as conn:
        await increment_taps_emitted(conn, person_id=pid, user_id=user_id)
        await conn.commit()

    async with pool.connection() as conn:
        await increment_taps_emitted(conn, person_id=pid, user_id=user_id)
        await conn.commit()

    async with pool.connection() as conn:
        st = await get_onboarding_state(conn, person_id=pid, user_id=user_id)
    assert st is not None
    assert st.taps_emitted == 2


async def test_flip_phase_is_sticky(pool):
    """After a successful flip to 'active', re-running flip_phase_if_complete
    is a no-op — the phase='onboarding' guard prevents re-entry."""
    pid = await _person(pool)
    user_id = uuid.uuid4()

    # Set up a row with voice anchor.
    async with pool.connection() as conn:
        await upsert_onboarding(
            conn, person_id=pid, user_id=user_id,
            voice_anchor_text="his son",
            voice_anchored_at=datetime.now(timezone.utc),
        )
        await conn.commit()

    # Stamp a first_moment.
    async with pool.connection() as conn:
        mid = await _insert_moment(conn, pid, user_id)
        await conn.commit()

    async with pool.connection() as conn:
        await conn.execute(
            MARK_FIRST_MOMENT_SQL,
            {"person_id": str(pid), "user_id": str(user_id), "moment_id": str(mid)},
        )
        await conn.commit()

    # First flip — should advance to 'active'.
    async with pool.connection() as conn:
        await flip_phase_if_complete(conn, person_id=pid, user_id=user_id)
        await conn.commit()

    async with pool.connection() as conn:
        st = await get_onboarding_state(conn, person_id=pid, user_id=user_id)
    assert st is not None
    assert st.phase == "active"

    # Second flip — should be a no-op; phase must remain 'active'.
    async with pool.connection() as conn:
        await flip_phase_if_complete(conn, person_id=pid, user_id=user_id)
        await conn.commit()

    async with pool.connection() as conn:
        st = await get_onboarding_state(conn, person_id=pid, user_id=user_id)
    assert st is not None
    assert st.phase == "active"


async def test_modal_dismissed_satisfies_connection(pool):
    """modal_dismissed_at (no voice anchor) counts as has_connection=True and
    is sufficient for the phase flip once a first moment is recorded."""
    pid = await _person(pool)
    user_id = uuid.uuid4()

    # Upsert with modal_dismissed_at only — no voice anchor.
    async with pool.connection() as conn:
        await upsert_onboarding(
            conn, person_id=pid, user_id=user_id,
            modal_dismissed_at=datetime.now(timezone.utc),
        )
        await conn.commit()

    # has_connection should be True even without a voice anchor.
    async with pool.connection() as conn:
        st = await get_onboarding_state(conn, person_id=pid, user_id=user_id)
    assert st is not None
    assert st.has_connection is True

    # Insert a moment and stamp it as the first moment.
    async with pool.connection() as conn:
        mid = await _insert_moment(conn, pid, user_id)
        await conn.commit()

    async with pool.connection() as conn:
        await conn.execute(
            MARK_FIRST_MOMENT_SQL,
            {"person_id": str(pid), "user_id": str(user_id), "moment_id": str(mid)},
        )
        await conn.commit()

    # Both conditions met — flip should advance to 'active'.
    async with pool.connection() as conn:
        await flip_phase_if_complete(conn, person_id=pid, user_id=user_id)
        await conn.commit()

    async with pool.connection() as conn:
        st = await get_onboarding_state(conn, person_id=pid, user_id=user_id)
    assert st is not None
    assert st.phase == "active"
