from __future__ import annotations

from datetime import datetime, timezone

from flashback.working_memory.keys import state_key


SESSION_ID = "11111111-2222-3333-4444-555555555555"
PERSON_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ROLE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _now() -> datetime:
    return datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


async def test_new_session_starts_at_zero(wm):
    await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())

    state = await wm.get_state(SESSION_ID)

    assert state.segments_pushed_this_session == 0


async def test_increment_segments_pushed_increments_by_one(wm):
    await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())

    assert await wm.increment_segments_pushed(SESSION_ID) == 1
    assert await wm.increment_segments_pushed(SESSION_ID) == 2

    state = await wm.get_state(SESSION_ID)
    assert state.segments_pushed_this_session == 2


async def test_increment_refreshes_ttl(wm, redis_client):
    await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
    await redis_client.expire(state_key(SESSION_ID), 5)

    await wm.increment_segments_pushed(SESSION_ID)

    ttl = await redis_client.ttl(state_key(SESSION_ID))
    assert ttl > 5
