"""Message-invitation tap for the tribute flow.

ONE way the in-chat "say it to them" card fires — the WARM CLIMAX
(one-time): on a warm story/deepen turn once the other checklist slots are
mostly filled, so the message lands as the emotional climax, not a cold
open (design 2026-06-14 section 5). The answer returns as the
message_answer sidecar and is polished into tributes.message_text, never
entering the transcript (so it can't be mined-and-lost by extraction).

The old FALLBACK path (re-offering every cooldown window once the message
was the only unfilled slot) is retired: the tribute card OUTSIDE chat now
owns that job — Node shows the question directly on the card and submits
via POST /tributes/{id}/message, so nobody is ever stuck below 100% and
the chat never nags (design 2026-07-15).

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
from flashback.tribute.invitation import resolve_invitation_copy
from flashback.tribute.progress import fetch_tribute_progress_async

log = structlog.get_logger("flashback.orchestrator")

MESSAGE_TAP_COOLDOWN_USER_TURNS = 2
# Floor on overall completion before the message is invited in chat. On a
# message-less campaign row this is the CEILING of the other two slots
# (stories 50 + signature 15), so it means "everything else is done" -- the
# message, worth 35 and the hard gate for the video, is the last thing asked.
#
# It was 40, on the reasoning that 40 meant the memories were substantially
# filled. It didn't: signature alone is worth 15, so 40-45% is reachable with a
# single qualifying story (prod 2026-07-28 had a legacy at exactly 40% with
# memories_count=1, eligible to be asked for its closing message).
#
# Note the coupling this creates: a legacy with stories maxed but NO trait tops
# out at 50 and is never invited in chat. The card lane outside chat has no
# gate (POST /tributes/{id}/message), so it still finishes -- but if in-chat
# asks start going missing, this is the reason.
MESSAGE_INVITATION_PERCENT_FLOOR = 65


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
                invitation_copy = await resolve_invitation_copy(
                    cur,
                    tribute_id=tribute_id,
                    person_id=str(state.person_id),
                    wm_campaign_slug=wm_state.current_tribute_campaign or None,
                )
        if progress is None:
            return

        # The message is a CAMPAIGN-only slot (two-meter model, design
        # 2026-07-22). The standalone keepsake is simplified -- it never asks
        # for "one thing to say", so the invitation never fires for it.
        if progress.kind != "campaign":
            log.info("message_tap.skipped", reason="standalone_no_message")
            return

        def _filled(key: str) -> bool:
            return any(s.key == key and s.filled for s in progress.slots)

        if _filled("message"):
            log.info("message_tap.skipped", reason="message_already_present")
            return

        # Warm climax (one-time): the ONE in-conversation moment this card
        # fires. The message-only-left fallback lives on the tribute card
        # outside chat now (POST /tributes/{id}/message) — never re-nag here.
        #
        # The message goes LAST: the floor is the ceiling of the other two slots
        # (see MESSAGE_INVITATION_PERCENT_FLOOR). It also used to require
        # _filled("appearance"), which deadlocked the meter outright -- that
        # slot stopped being scored in migration 0050.
        warm_climax = (
            not wm_state.message_invitation_asked
            and state.intent_result is not None
            and state.intent_result.intent in {"story", "deepen"}
            and state.effective_temperature == "high"
            and progress.percent >= MESSAGE_INVITATION_PERCENT_FLOOR
        )

        if not warm_climax:
            log.info("message_tap.skipped", reason="not_warranted")
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
            path="warm_climax",
        )
