"""persist_message_answer routes the sidecar to the tribute row and
clears the WM signal; a no-pending-tap answer is ignored."""

from __future__ import annotations

import json
from uuid import UUID

import flashback.tribute.message_capture as mc
from flashback.http.models import MessageAnswerInput
from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.repository import ensure_open_tribute_async
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)

_SID = UUID("11111111-1111-1111-1111-111111111111")


class _FakeWM:
    def __init__(self, state) -> None:
        self._state = state
        self.cleared = False

    async def get_state(self, _sid):
        return self._state

    async def clear_pending_message(self, _sid):
        self.cleared = True


class _State:
    def __init__(self, *, pending: str, tribute_id: str) -> None:
        self.signal_pending_message = pending
        self.current_tribute_id = tribute_id


async def _make_person_theme_tribute(pool) -> tuple[str, str]:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Dad') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
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


async def test_ignored_when_no_pending(async_pool) -> None:
    wm = _FakeWM(_State(pending="", tribute_id=""))
    await mc.persist_message_answer(
        session_id=_SID,
        person_id=_SID,
        answer=MessageAnswerInput(free_text="hi"),
        wm=wm,
        db_pool=async_pool,
        settings=None,
    )
    assert wm.cleared is False  # ignored before any clear


async def test_records_polished_message(monkeypatch, async_pool) -> None:
    person_id, tribute_id = await _make_person_theme_tribute(async_pool)

    async def _fake_polish(**kwargs):
        return "Polished: " + kwargs["raw_text"]

    monkeypatch.setattr(mc, "polish_message", _fake_polish)
    wm = _FakeWM(
        _State(pending=json.dumps({"kind": "message"}), tribute_id=tribute_id)
    )

    await mc.persist_message_answer(
        session_id=_SID,
        person_id=person_id,
        answer=MessageAnswerInput(free_text="i never said thanks"),
        wm=wm,
        db_pool=async_pool,
        settings=None,
    )

    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT message_text FROM tributes WHERE id = %s", (tribute_id,)
            )
            (msg,) = await cur.fetchone()
    assert msg == "Polished: i never said thanks"
    assert wm.cleared is True


async def test_skipped_clears_without_writing(monkeypatch, async_pool) -> None:
    person_id, tribute_id = await _make_person_theme_tribute(async_pool)
    wm = _FakeWM(
        _State(pending=json.dumps({"kind": "message"}), tribute_id=tribute_id)
    )
    await mc.persist_message_answer(
        session_id=_SID,
        person_id=person_id,
        answer=MessageAnswerInput(skipped=True),
        wm=wm,
        db_pool=async_pool,
        settings=None,
    )
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT message_text FROM tributes WHERE id = %s", (tribute_id,)
            )
            (msg,) = await cur.fetchone()
    assert msg is None
    assert wm.cleared is True
