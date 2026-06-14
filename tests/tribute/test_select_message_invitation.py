"""Gating + happy path for select_message_invitation.

Negative gates short-circuit before any DB call, so they use a dummy
tribute id. The happy path builds a real tribute with appearance +
3 qualifying memories (percent 60, message empty) and asserts a
``message`` tap is emitted.
"""

from __future__ import annotations

import json

from flashback.orchestrator.steps.select_message_invitation import (
    select_message_invitation,
)
from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.repository import ensure_open_tribute_async
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)


class _Intent:
    def __init__(self, intent: str) -> None:
        self.intent = intent


class _WMState:
    def __init__(self, **kw) -> None:
        self.current_tribute_id = kw.get("current_tribute_id", "")
        self.message_invitation_asked = kw.get("message_invitation_asked", False)
        self.user_turns_since_last_tap = kw.get("user_turns_since_last_tap", 9)


class _TurnState:
    def __init__(self, *, intent="deepen", temp="high", taps=None, wm=None) -> None:
        self.intent_result = _Intent(intent)
        self.effective_temperature = temp
        self.taps = taps or []
        self.working_memory_state = wm
        self.session_id = "s1"


class _WM:
    def __init__(self) -> None:
        self.emitted = False

    async def record_message_invitation_emitted(self, *, session_id, payload_json):
        self.emitted = True


class _Deps:
    def __init__(self, pool) -> None:
        self.working_memory = _WM()
        self.db_pool = pool


async def _make_ready_tribute(pool) -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Dad') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                gt = json.dumps(
                    {
                        "region": {"value": "South India"},
                        "birth_era": {"value": "1950s"},
                        "attire": {"value": "white cotton shirt"},
                    }
                )
                await cur.execute(
                    "UPDATE persons SET ground_truth = %s WHERE id = %s",
                    (gt, person_id),
                )
                for i in range(3):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details) VALUES (%s, %s, %s, %s)",
                        (person_id, f"m{i}", "n", "the smell of diesel and rain"),
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
    return tribute_id


async def test_happy_path_emits_message_tap(async_pool) -> None:
    tribute_id = await _make_ready_tribute(async_pool)
    deps = _Deps(async_pool)
    state = _TurnState(wm=_WMState(current_tribute_id=tribute_id))
    await select_message_invitation(state, deps)
    assert len(state.taps) == 1
    assert state.taps[0].kind == "message"
    assert deps.working_memory.emitted is True


async def test_wrong_intent_no_tap(async_pool) -> None:
    deps = _Deps(async_pool)
    state = _TurnState(intent="switch", wm=_WMState(current_tribute_id="t1"))
    await select_message_invitation(state, deps)
    assert state.taps == []


async def test_not_warm_no_tap(async_pool) -> None:
    deps = _Deps(async_pool)
    state = _TurnState(temp="low", wm=_WMState(current_tribute_id="t1"))
    await select_message_invitation(state, deps)
    assert state.taps == []


async def test_already_asked_no_tap(async_pool) -> None:
    deps = _Deps(async_pool)
    state = _TurnState(
        wm=_WMState(current_tribute_id="t1", message_invitation_asked=True)
    )
    await select_message_invitation(state, deps)
    assert state.taps == []


async def test_no_tribute_no_tap(async_pool) -> None:
    deps = _Deps(async_pool)
    state = _TurnState(wm=_WMState(current_tribute_id=""))
    await select_message_invitation(state, deps)
    assert state.taps == []
