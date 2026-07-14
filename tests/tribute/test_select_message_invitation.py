"""Gating + both firing paths for select_message_invitation.

Two ways the message card fires:
  - WARM CLIMAX (one-time): warm story/deepen turn, other slots mostly
    filled. Built on a tribute with memories + appearance but NO signature
    (percent 60), so only the warm path can fire it -- the fallback can't.
  - FALLBACK (re-offering): the message is the ONLY unfilled slot. Built on
    a tribute with memories + appearance + signature all filled, message
    empty -- fires on a cold clarify turn and even after `asked`.

Cheap gates (no tribute / other tap pending / cooldown) short-circuit
before any DB call.
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
        self.current_tribute_campaign = kw.get("current_tribute_campaign", "")


class _TurnState:
    def __init__(self, *, intent="deepen", temp="high", taps=None, wm=None,
                 person_id="00000000-0000-0000-0000-000000000001") -> None:
        self.intent_result = _Intent(intent)
        self.effective_temperature = temp
        self.taps = taps or []
        self.working_memory_state = wm
        self.session_id = "s1"
        self.person_id = person_id


class _WM:
    def __init__(self) -> None:
        self.emitted = False

    async def record_message_invitation_emitted(self, *, session_id, payload_json):
        self.emitted = True


class _Deps:
    def __init__(self, pool) -> None:
        self.working_memory = _WM()
        self.db_pool = pool


async def _seed_tribute(pool, *, with_signature: bool) -> str:
    """A tribute with 3 qualifying memories + appearance ground truth and,
    optionally, a trait (so the signature slot fills). Message stays empty."""
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
                if with_signature:
                    await cur.execute(
                        "INSERT INTO traits (person_id, name, status) "
                        "VALUES (%s, 'patient', 'active')",
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


# ---------------------------------------------------------------------------
# Warm-climax path: memories + appearance filled, signature NOT (percent 60).
# ---------------------------------------------------------------------------


async def test_warm_climax_emits_message_tap(async_pool) -> None:
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=False)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, wm=_WMState(current_tribute_id=tribute_id))
    await select_message_invitation(state, deps)
    assert len(state.taps) == 1
    assert state.taps[0].kind == "message"
    assert deps.working_memory.emitted is True


async def test_warm_gate_wrong_intent_no_tap(async_pool) -> None:
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=False)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, intent="switch", wm=_WMState(current_tribute_id=tribute_id))
    await select_message_invitation(state, deps)
    assert state.taps == []


async def test_warm_gate_low_temp_no_tap(async_pool) -> None:
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=False)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, temp="low", wm=_WMState(current_tribute_id=tribute_id))
    await select_message_invitation(state, deps)
    assert state.taps == []


async def test_warm_gate_already_asked_no_tap(async_pool) -> None:
    # Signature still missing, so the fallback can't rescue it: a one-time
    # warm card that's already been asked stays silent.
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=False)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, 
        wm=_WMState(current_tribute_id=tribute_id, message_invitation_asked=True)
    )
    await select_message_invitation(state, deps)
    assert state.taps == []


# ---------------------------------------------------------------------------
# Fallback path: memories + appearance + signature all filled, message empty.
# ---------------------------------------------------------------------------


async def test_fallback_fires_on_cold_clarify_turn(async_pool) -> None:
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=True)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, 
        intent="clarify", temp="low", wm=_WMState(current_tribute_id=tribute_id)
    )
    await select_message_invitation(state, deps)
    assert len(state.taps) == 1
    assert state.taps[0].kind == "message"
    assert deps.working_memory.emitted is True


async def test_fallback_reoffers_even_after_asked(async_pool) -> None:
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=True)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, 
        intent="clarify",
        temp="low",
        wm=_WMState(current_tribute_id=tribute_id, message_invitation_asked=True),
    )
    await select_message_invitation(state, deps)
    assert len(state.taps) == 1
    assert state.taps[0].kind == "message"


async def test_fallback_respects_cooldown(async_pool) -> None:
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=True)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, 
        intent="clarify",
        temp="low",
        wm=_WMState(current_tribute_id=tribute_id, user_turns_since_last_tap=1),
    )
    await select_message_invitation(state, deps)
    assert state.taps == []


# ---------------------------------------------------------------------------
# Cheap gates -- short-circuit before any DB call.
# ---------------------------------------------------------------------------


async def test_other_tap_pending_no_tap(async_pool) -> None:
    deps = _Deps(async_pool)
    state = _TurnState(taps=["x"], wm=_WMState(current_tribute_id="t1"))
    await select_message_invitation(state, deps)
    assert state.taps == ["x"]


async def test_no_tribute_no_tap(async_pool) -> None:
    deps = _Deps(async_pool)
    state = _TurnState(wm=_WMState(current_tribute_id=""))
    await select_message_invitation(state, deps)
    assert state.taps == []
