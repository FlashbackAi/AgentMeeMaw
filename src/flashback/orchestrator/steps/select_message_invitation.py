"""One-time message-invitation tap for the tribute flow.

Emits a single "say it to them" tap when the contributor is in a tribute
flow, the conversation is warm, and the other checklist slots are mostly
filled -- so the message lands as the emotional climax, not a cold open
(design 2026-06-14 section 5). The answer returns as the message_answer
sidecar and is polished into tributes.message_text; it never enters the
transcript.

The invitation copy is neutral here; Plan 4's campaign skin overrides it.
"""

from __future__ import annotations

import json

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.protocol import Tap
from flashback.orchestrator.state import TurnState
from flashback.tribute.campaigns import resolve_campaign
from flashback.tribute.progress import fetch_tribute_progress_async
from flashback.tribute.theme import MESSAGE_INVITATION_COPY

log = structlog.get_logger("flashback.orchestrator")

MESSAGE_TAP_COOLDOWN_USER_TURNS = 2
# Floor on overall completion before we invite the message. Memories +
# appearance + signature alone (no message) top out at 70; requiring 40
# means at least a couple of memories plus another slot are in place.
MESSAGE_INVITATION_PERCENT_FLOOR = 40


async def select_message_invitation(state: TurnState, deps: OrchestratorDeps) -> None:
    """Emit the one-time tribute message-invitation tap, if warranted."""

    with timed_step(log, "select_message_invitation"):
        if state.intent_result is None or state.intent_result.intent not in {
            "story",
            "deepen",
        }:
            return
        if state.taps:
            log.info("message_tap.skipped", reason="other_tap_pending")
            return
        # We WANT a warm moment for the confession (opposite of GT taps).
        if state.effective_temperature != "high":
            log.info("message_tap.skipped", reason="not_warm_enough")
            return

        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state

        tribute_id = wm_state.current_tribute_id
        if not tribute_id:
            return  # not in a tribute flow
        if wm_state.message_invitation_asked:
            log.info("message_tap.skipped", reason="already_asked")
            return
        if wm_state.user_turns_since_last_tap < MESSAGE_TAP_COOLDOWN_USER_TURNS:
            log.info("message_tap.skipped", reason="cooldown")
            return

        async with deps.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                progress = await fetch_tribute_progress_async(
                    cur, tribute_id=tribute_id
                )
        if progress is None:
            return

        def _filled(key: str) -> bool:
            return any(s.key == key and s.filled for s in progress.slots)

        if _filled("message"):
            log.info("message_tap.skipped", reason="message_already_present")
            return
        # Mostly-filled gate: appearance present and a completion floor.
        if not _filled("appearance"):
            log.info("message_tap.skipped", reason="slots_not_ready")
            return
        if progress.percent < MESSAGE_INVITATION_PERCENT_FLOOR:
            log.info("message_tap.skipped", reason="too_sparse")
            return

        # Skin copy (e.g. Father's Day) overrides the neutral default.
        campaign = resolve_campaign(wm_state.current_tribute_campaign or None)
        invitation_copy = campaign.message_card_copy or MESSAGE_INVITATION_COPY

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
        log.info("message_tap.selected", tribute_id=tribute_id)
