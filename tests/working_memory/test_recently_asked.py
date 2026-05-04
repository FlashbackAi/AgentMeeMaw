from __future__ import annotations

from flashback.working_memory.keys import asked_key

SESSION_ID = "11111111-2222-3333-4444-555555555555"


async def test_append_asked_question_adds_to_list(wm):
    await wm.append_asked_question(SESSION_ID, "q1")

    assert await wm.get_recently_asked_question_ids(SESSION_ID) == ["q1"]


async def test_recently_asked_trims_to_last_five(wm):
    for i in range(6):
        await wm.append_asked_question(SESSION_ID, f"q{i}")

    assert await wm.get_recently_asked_question_ids(SESSION_ID) == [
        "q1",
        "q2",
        "q3",
        "q4",
        "q5",
    ]


async def test_recently_asked_returns_oldest_first(wm):
    for qid in ["older", "middle", "newer"]:
        await wm.append_asked_question(SESSION_ID, qid)

    assert await wm.get_recently_asked_question_ids(SESSION_ID) == [
        "older",
        "middle",
        "newer",
    ]


async def test_recently_asked_ttl_is_refreshed(wm, redis_client):
    await wm.append_asked_question(SESSION_ID, "q1")
    await redis_client.expire(asked_key(SESSION_ID), 5)

    await wm.append_asked_question(SESSION_ID, "q2")

    ttl = await redis_client.ttl(asked_key(SESSION_ID))
    assert ttl > 5
