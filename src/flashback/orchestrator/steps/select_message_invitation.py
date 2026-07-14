"""Message-invitation tap for the tribute flow.

Two ways the "say it to them" card fires; the answer always returns as the
message_answer sidecar and is polished into tributes.message_text, never
entering the transcript (so it can't be mined-and-lost by extraction):

  - WARM CLIMAX (one-time): on a warm story/deepen turn once the other
    checklist slots are mostly filled, so the message lands as the
    emotional climax, not a cold open (design 2026-06-14 section 5).
  - FALLBACK (re-offering): once the message is the ONLY unfilled slot,
    the card fires regardless of intent/temperature and keeps re-offering
    every cooldown window until it's answered. `message_present` is
    required for `ready`, so without this a contributor whose warm turns
    never line up is stuck below 100% forever.

The invitation copy resolves campaign -> relationship profile -> neutral
(tribute CRM, spec 2026-07-14): the campaign the tribute was created under
wins, else the person's relationship-profile copy, else the neutral line.
"""

from __future__ import annotations

import json

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.protocol import Tap
from flashback.orchestrator.state import TurnState
from flashback.tribute.config_repository import (
    fetch_campaign_by_id,
    fetch_profile_by_group,
    resolve_campaign_db,
)
from flashback.tribute.progress import fetch_tribute_progress_async
from flashback.tribute.repository import fetch_tribute_campaign_id_async
from flashback.tribute.theme import MESSAGE_INVITATION_COPY

log = structlog.get_logger("flashback.orchestrator")

MESSAGE_TAP_COOLDOWN_USER_TURNS = 2
# Floor on overall completion before we invite the message. Memories +
# appearance + signature alone (no message) top out at 70; requiring 40
# means at least a couple of memories plus another slot are in place.
MESSAGE_INVITATION_PERCENT_FLOOR = 40


async def _resolve_invitation_copy(
    cur,
    *,
    tribute_id: str,
    person_id: str,
    wm_campaign_slug: str | None,
) -> str:
    """campaign copy -> profile copy -> neutral. Best-effort, never raises."""
    try:
        campaign = None
        campaign_id = await fetch_tribute_campaign_id_async(
            cur, tribute_id=tribute_id
        )
        if campaign_id:
            campaign = await fetch_campaign_by_id(cur, campaign_id)
        if campaign is None:
            campaign = await resolve_campaign_db(cur, wm_campaign_slug)
        if campaign.message_card_copy:
            return campaign.message_card_copy

        await cur.execute(
            "SELECT relationship_group FROM persons WHERE id = %s", (person_id,)
        )
        row = await cur.fetchone()
        group = (row[0] if row else None) or "other"
        profile = await fetch_profile_by_group(cur, group)
        if profile is not None and profile.message_invitation_copy:
            return profile.message_invitation_copy
    except Exception:
        log.warning("message_tap.copy_resolution_failed", exc_info=True)
    return MESSAGE_INVITATION_COPY


async def select_message_invitation(state: TurnState, deps: OrchestratorDeps) -> None:
    """Emit the one-time tribute message-invitation tap, if warranted."""

    with timed_step(log, "select_message_invitation"):
        # Cheap gates first -- these short-circuit before any DB call. We no
        # longer gate on intent/temperature here: the fallback path must reach
        # the progress read even on a cold clarify turn to learn whether the
        # message is the only thing left.
        if state.taps:
            log.info("message_tap.skipped", reason="other_tap_pending")
            return

        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state

        tribute_id = wm_state.current_tribute_id
        if not tribute_id:
            return  # not in a tribute flow
        if wm_state.user_turns_since_last_tap < MESSAGE_TAP_COOLDOWN_USER_TURNS:
            log.info("message_tap.skipped", reason="cooldown")
            return

        async with deps.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                progress = await fetch_tribute_progress_async(
                    cur, tribute_id=tribute_id
                )
                invitation_copy = await _resolve_invitation_copy(
                    cur,
                    tribute_id=tribute_id,
                    person_id=str(state.person_id),
                    wm_campaign_slug=wm_state.current_tribute_campaign or None,
                )
        if progress is None:
            return

        def _filled(key: str) -> bool:
            return any(s.key == key and s.filled for s in progress.slots)

        if _filled("message"):
            log.info("message_tap.skipped", reason="message_already_present")
            return

        # Fallback: the message is the ONLY unfilled slot. Fire regardless of
        # intent/temperature and re-offer every cooldown window (ignore the
        # one-time `message_invitation_asked` flag) -- `message_present` is
        # required for `ready`, so this is what guarantees the contributor is
        # never permanently stuck below 100%.
        only_slot_left = (
            _filled("memories")
            and _filled("appearance")
            and _filled("signature")
        )

        # Warm climax (one-time): the preferred in-conversation moment.
        warm_climax = (
            not wm_state.message_invitation_asked
            and state.intent_result is not None
            and state.intent_result.intent in {"story", "deepen"}
            and state.effective_temperature == "high"
            and _filled("appearance")
            and progress.percent >= MESSAGE_INVITATION_PERCENT_FLOOR
        )

        if not (only_slot_left or warm_climax):
            log.info(
                "message_tap.skipped",
                reason="not_warranted",
                only_slot_left=only_slot_left,
            )
            return

        tap = Tap(
            question_id=None,
            text=invitation_copy,
            dimension="",
            options=[],
            kind="message",
            field=None,
        )
        state.taps = [tap]
        await deps.working_memory.record_message_invitation_emitted(
            session_id=str(state.session_id),
            payload_json=json.dumps({"kind": "message", "text": invitation_copy}),
        )
        log.info(
            "message_tap.selected",
            tribute_id=tribute_id,
            path="fallback" if only_slot_left and not warm_climax else "warm_climax",
        )
