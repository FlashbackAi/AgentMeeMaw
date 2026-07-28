"""Gating for select_message_invitation — warm-climax only.

The card fires exactly once, on a warm story/deepen turn with the other
slots mostly filled. The old message-only-left FALLBACK (re-offer every
cooldown) is retired: the tribute card outside chat owns that job via
POST /tributes/{id}/message (design 2026-07-15), so a cold clarify turn
must never emit the card — even when the message is the only gap.

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


async def _seed_tribute(
    pool,
    *,
    with_signature: bool,
    with_appearance: bool = True,
    moments: int = 3,
) -> str:
    """A tribute with ``moments`` qualifying memories (3 = the story floor, so
    the memories slot reads FILLED) and, optionally, appearance ground truth and
    a trait (so the signature slot fills). Message stays empty.

    Appearance is no longer a scored slot (migration 0050) and no longer gates
    the invitation, so ``with_appearance`` is purely about whether the person
    has physical ground truth — it must NOT change whether the tap fires."""
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Dad') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                if with_appearance:
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
                for i in range(moments):
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
                # The message invitation is a CAMPAIGN-only flow (two-meter
                # model): stamp a campaign so the row is a campaign meter, not
                # the message-less standalone.
                await cur.execute(
                    "INSERT INTO tribute_campaigns (slug, display_name) "
                    "VALUES (%s, 'FD Test') RETURNING id::text",
                    (f"fd_{person_id.replace('-', '')[:12]}",),
                )
                campaign_id = (await cur.fetchone())[0]
                tribute_id = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=campaign_id,
                )
    return person_id, tribute_id


# ---------------------------------------------------------------------------
# Warm-climax path: percent >= 65, which on a message-less campaign row is
# the CEILING of the other two slots (stories 50 + signature 15) -- i.e. the
# message is asked last, once everything else is done. Appearance
# participates in neither the meter nor the gate (migration 0050).
# ---------------------------------------------------------------------------


async def test_warm_climax_emits_message_tap(async_pool) -> None:
    # with_signature=True is REQUIRED by the 65 floor: stories max out at 50, so
    # 65 means stories + signature are both done and the message is all that's
    # left. See MESSAGE_INVITATION_PERCENT_FLOOR.
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=True)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, wm=_WMState(current_tribute_id=tribute_id))
    await select_message_invitation(state, deps)
    assert len(state.taps) == 1
    assert state.taps[0].kind == "message"
    assert deps.working_memory.emitted is True


async def test_warm_climax_fires_without_appearance(async_pool) -> None:
    """Regression (migration 0050): the message invitation must fire even when
    the subject has NO appearance ground truth. This is the deadlock that froze
    campaign tributes at 50% — a no-appearance legacy could never be prompted
    for its message and so never reached `ready`."""
    person_id, tribute_id = await _seed_tribute(
        async_pool, with_signature=True, with_appearance=False
    )
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, wm=_WMState(current_tribute_id=tribute_id))
    await select_message_invitation(state, deps)
    assert len(state.taps) == 1
    assert state.taps[0].kind == "message"


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
# Fallback retirement: message-only-left must NOT fire the in-chat card.
# ---------------------------------------------------------------------------


async def test_message_only_left_cold_turn_stays_silent(async_pool) -> None:
    """The old fallback trigger — retired. The tribute card outside chat
    owns this case now (POST /tributes/{id}/message)."""
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=True)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id,
        intent="clarify", temp="low", wm=_WMState(current_tribute_id=tribute_id)
    )
    await select_message_invitation(state, deps)
    assert state.taps == []


async def test_message_only_left_never_reoffers_after_asked(async_pool) -> None:
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=True)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id,
        intent="clarify",
        temp="low",
        wm=_WMState(current_tribute_id=tribute_id, message_invitation_asked=True),
    )
    await select_message_invitation(state, deps)
    assert state.taps == []


async def test_warm_climax_still_fires_when_all_other_slots_full(async_pool) -> None:
    """Retiring the fallback must not break the warm path on a rich tribute."""
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=True)
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, wm=_WMState(current_tribute_id=tribute_id))
    await select_message_invitation(state, deps)
    assert len(state.taps) == 1
    assert state.taps[0].kind == "message"


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


async def test_warm_gate_thin_story_pool_no_tap(async_pool) -> None:
    """The message goes LAST -- after the stories, not alongside the first one.

    Prod 2026-07-28: a campaign legacy sat at exactly 40% with memories_count=1
    and was eligible for the closing-message card. The old gate was
    `percent >= 40`, on the reasoning that 40 meant the memories were
    substantially filled -- but signature alone is worth 15, so 40-45% is
    reachable with a single qualifying memory. The gate is the memories slot now.
    """
    person_id, tribute_id = await _seed_tribute(
        async_pool, with_signature=True, moments=1
    )
    # Give the one memory its depth bonuses (long sensory + a year), which is
    # what carried the real prod row over 40 on a single story.
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE moments SET sensory_details = %s, time_anchor = %s "
                "WHERE person_id = %s",
                (
                    "diesel smoke off the road, wet earth after the first rain, "
                    "and the radio playing thin through the workshop wall",
                    json.dumps({"year": 2009}),
                    person_id,
                ),
            )
    # The meter really is in the band the old floor would have let through.
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT percent, memories_count, signature_present "
                "FROM tribute_status WHERE id = %s",
                (tribute_id,),
            )
            percent, memories_count, signature = await cur.fetchone()
    assert percent >= 40 and memories_count == 1 and signature is True

    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, wm=_WMState(current_tribute_id=tribute_id))
    await select_message_invitation(state, deps)
    assert state.taps == []
    assert deps.working_memory.emitted is False


async def test_warm_climax_fires_once_the_stories_are_in(async_pool) -> None:
    """The same legacy, once it reaches the 3-story floor, does get asked."""
    person_id, tribute_id = await _seed_tribute(
        async_pool, with_signature=True, moments=3
    )
    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, wm=_WMState(current_tribute_id=tribute_id))
    await select_message_invitation(state, deps)
    assert len(state.taps) == 1
    assert state.taps[0].kind == "message"


async def test_warm_gate_no_signature_stays_silent(async_pool) -> None:
    """The coupling the 65 floor creates, pinned deliberately.

    Stories cap at 50, so a legacy with the stories in but NO extracted trait
    can never clear a 65 floor and is never invited in chat. That is accepted:
    the card lane outside chat (POST /tributes/{id}/message) has no gate, so the
    contributor still finishes. If in-chat message asks ever go missing for a
    legacy with plenty of stories, look here first.
    """
    person_id, tribute_id = await _seed_tribute(async_pool, with_signature=False)
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT percent, memories_count, signature_present "
                "FROM tribute_status WHERE id = %s",
                (tribute_id,),
            )
            percent, memories_count, signature = await cur.fetchone()
    assert memories_count >= 3 and signature is False and percent == 50

    deps = _Deps(async_pool)
    state = _TurnState(person_id=person_id, wm=_WMState(current_tribute_id=tribute_id))
    await select_message_invitation(state, deps)
    assert state.taps == []
