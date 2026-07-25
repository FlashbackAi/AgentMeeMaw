"""Contextual ground-truth tap selection for story / deepen turns.

A strict extension of the turn pipeline (design 2026-06-11): it only
ever ATTACHES a tap to the reply. It never alters intent handling,
retrieval, or steady question selection, and it never fires on switch
(that surface belongs to the question bank).
"""

from __future__ import annotations

import json

import structlog

from flashback.ground_truth.registry import REGISTRY
from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.selection_llm import (
    derive_anchor_chips,
    select_ground_truth_question,
)
from flashback.ground_truth.store import fetch_ground_truth
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.protocol import Tap
from flashback.orchestrator.state import TurnState

log = structlog.get_logger("flashback.orchestrator")

GT_TAPS_PER_SESSION_CAP = 9
MIN_USER_TURNS_BEFORE_GT_TAP = 9
# With a cap this high, per-turn pacing comes from the shared
# 2-user-turn tap cooldown plus the LLM skip-gate (which only asks what
# the live story naturally touches).
GT_TAP_COOLDOWN_USER_TURNS = 2


async def select_ground_truth_tap(state: TurnState, deps: OrchestratorDeps) -> None:
    """Emit at most one ground-truth / segment-anchor tap per turn,
    capped per session and cooled down between taps."""

    with timed_step(log, "select_ground_truth_tap"):
        if state.intent_result is None or state.intent_result.intent not in {
            "story",
            "deepen",
        }:
            return
        # (The old high-temperature skip was removed: the tap cards may now
        # surface on emotionally-high turns too. Note this step runs BEFORE
        # select_message_invitation, which requires high temperature — so on a
        # high-temp turn a GT tap can now claim the turn the message card used
        # to get. The persistent message ask lives on Node's tribute card, so
        # in-chat message starvation is acceptable; revisit if that changes.)
        if state.taps:
            log.info("gt_tap.skipped", reason="other_tap_pending")
            return

        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state
        if wm_state.gt_taps_emitted_this_session >= GT_TAPS_PER_SESSION_CAP:
            log.info("gt_tap.skipped", reason="session_cap")
            return
        if wm_state.user_turns_since_last_tap < GT_TAP_COOLDOWN_USER_TURNS:
            # Shared cooldown with coverage taps: never tap cards on
            # back-to-back user turns.
            log.info(
                "gt_tap.skipped",
                reason="cooldown",
                user_turns_since_last_tap=wm_state.user_turns_since_last_tap,
            )
            return

        transcript = state.transcript or await deps.working_memory.get_transcript(
            str(state.session_id)
        )
        state.transcript = transcript
        user_turn_count = sum(1 for turn in transcript if turn.role == "user")
        if user_turn_count < MIN_USER_TURNS_BEFORE_GT_TAP:
            log.info(
                "gt_tap.skipped",
                reason="too_early",
                user_turns=user_turn_count,
            )
            return

        ground_truth = await fetch_ground_truth(deps.db_pool, state.person_id)
        declined = set(wm_state.gt_declined_fields)
        unknown_fields = [
            f
            for f in REGISTRY
            if f.askable and f.key not in ground_truth and f.key not in declined
        ]
        anchor_allowed = not wm_state.segment_anchor_answer
        if not unknown_fields and not anchor_allowed:
            log.info("gt_tap.skipped", reason="nothing_to_ask")
            return

        recent = [(t.role, t.content) for t in transcript[-12:]]
        result = await select_ground_truth_question(
            settings=deps.settings,
            person_name=state.person_name,
            person_relationship=state.person_relationship,
            unknown_fields=unknown_fields,
            known_block=render_ground_truth_block(ground_truth, "responder"),
            rolling_summary=wm_state.rolling_summary or "",
            recent_turns=recent,
            anchor_allowed=anchor_allowed,
        )
        if result is None:
            log.info("gt_tap.skipped", reason="llm_skip_or_failure")
            return

        if result["action"] == "ask_anchor":
            kind, field = "segment_anchor", None
            birth_era_entry = ground_truth.get("birth_era") or {}
            options = (
                derive_anchor_chips(birth_era_entry.get("value"))
                if birth_era_entry.get("value")
                else [str(o) for o in (result.get("options") or [])][:4]
            )
        else:
            kind, field = "ground_truth", str(result["field"])
            options = [str(o) for o in (result.get("options") or [])][:4]

        question_text = str(result["question_text"]).strip()
        tap = Tap(
            question_id=None,
            text=question_text,
            dimension="",
            options=options,
            kind=kind,
            field=field,
        )
        state.taps = [tap]
        await deps.working_memory.record_gt_tap_emitted(
            session_id=str(state.session_id),
            payload_json=json.dumps(
                {"kind": kind, "field": field, "question_text": question_text}
            ),
            question_text=question_text,
        )
        log.info("gt_tap.selected", kind=kind, field=field)
