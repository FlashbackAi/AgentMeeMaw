"""POST /tributes/{id}/message — the finish-without-chat lane.

Card answer -> polish -> tribute row -> fresh progress in one round trip.
The polish LLM is patched to identity (settings=None path inside
polish_message returns the cleaned raw text when no LLM is reachable —
here we patch to keep it deterministic).
"""

from __future__ import annotations

import json

import pytest

from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute import message_capture
from flashback.tribute.repository import ensure_open_tribute_async
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)

_HEADERS = {"X-Service-Token": "test-token"}

pytestmark = pytest.mark.anyio


async def _identity_polish(**kw):
    return (kw.get("raw_text") or "").strip()


async def _seed(pool, *, relationship: str | None = None,
                relationship_group: str | None = None) -> tuple[str, str]:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name, relationship, relationship_group) "
                    "VALUES ('Arjun', %s, %s) RETURNING id::text",
                    (relationship, relationship_group),
                )
                person_id = (await cur.fetchone())[0]
                gt = json.dumps(
                    {
                        "region": {"value": "Mumbai"},
                        "birth_era": {"value": "1990s"},
                        "attire": {"value": "denim jacket"},
                    }
                )
                await cur.execute(
                    "UPDATE persons SET ground_truth = %s WHERE id = %s",
                    (gt, person_id),
                )
                # Deep moments (>80 chars sensory + year anchor) so the
                # 0030 depth-weighted percent can actually reach 100.
                long_sensory = (
                    "chai steam and monsoon rain on the hostel steps, his "
                    "cracked phone screen, the smell of vada pav at the "
                    "corner stall after the 6 a.m. train"
                )
                for i in range(3):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details, time_anchor) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (person_id, f"m{i}", "n", long_sensory,
                         json.dumps({"year": 2009})),
                    )
                await cur.execute(
                    "INSERT INTO traits (person_id, name, status) "
                    "VALUES (%s, 'loyal', 'active')",
                    (person_id,),
                )
                theme_id = await ensure_tribute_theme_async(
                    cur,
                    person_id=person_id,
                    slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION,
                )
                tribute_id = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
    return person_id, tribute_id


async def test_message_fills_slot_and_returns_progress(
    client_with_db, async_db_pool, monkeypatch
) -> None:
    monkeypatch.setattr(message_capture, "polish_message", _identity_polish)
    person_id, tribute_id = await _seed(async_db_pool)

    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/message",
        json={"person_id": person_id, "text": "You were my whole childhood."},
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    message_slot = next(s for s in body["slots"] if s["key"] == "message")
    assert message_slot["filled"] is True
    assert body["percent"] == 100
    assert body["ready"] is True

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT message_text, message_source_turns FROM tributes "
                "WHERE id = %s",
                (tribute_id,),
            )
            text, source = await cur.fetchone()
    assert text == "You were my whole childhood."
    assert source[0]["source"] == "tribute_card"


async def test_message_hint_uses_relationship_profile_copy(
    client_with_db, async_db_pool, monkeypatch
) -> None:
    """No campaign stamped -> the friend profile's invitation line shows."""
    monkeypatch.setattr(message_capture, "polish_message", _identity_polish)
    person_id, tribute_id = await _seed(
        async_db_pool, relationship="best friend", relationship_group="friend"
    )
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/message",
        json={"person_id": person_id, "text": "Thanks for every rescue, idiot."},
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    message_slot = next(s for s in resp.json()["slots"] if s["key"] == "message")
    # Seeded friend-profile invitation copy (migration 0039).
    assert "Friends say everything" in message_slot["hint"]


async def test_404_on_person_mismatch(client_with_db, async_db_pool) -> None:
    _person_id, tribute_id = await _seed(async_db_pool)
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/message",
        json={
            "person_id": "00000000-0000-0000-0000-00000000dead",
            "text": "hello",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 404


async def test_409_when_already_complete(
    client_with_db, async_db_pool, monkeypatch
) -> None:
    monkeypatch.setattr(message_capture, "polish_message", _identity_polish)
    person_id, tribute_id = await _seed(async_db_pool)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE tributes SET status = 'complete' WHERE id = %s",
                (tribute_id,),
            )
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/message",
        json={"person_id": person_id, "text": "too late"},
        headers=_HEADERS,
    )
    assert resp.status_code == 409


async def test_reanswer_replaces_message(
    client_with_db, async_db_pool, monkeypatch
) -> None:
    monkeypatch.setattr(message_capture, "polish_message", _identity_polish)
    person_id, tribute_id = await _seed(async_db_pool)
    for text in ("First try.", "Second, better try."):
        resp = await client_with_db.post(
            f"/tributes/{tribute_id}/message",
            json={"person_id": person_id, "text": text},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT message_text FROM tributes WHERE id = %s", (tribute_id,)
            )
            (text,) = await cur.fetchone()
    assert text == "Second, better try."
