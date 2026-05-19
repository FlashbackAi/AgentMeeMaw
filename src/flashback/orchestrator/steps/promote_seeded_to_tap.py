"""Promote a steady-selector seeded question into a tap chip.

In starter phase the contributor expects the archetype-style tap UX to
continue mid-chat: every bot-suggested question should arrive as a
tappable card, not buried in the bot's reply. This step runs after
`select_question`; if a question was seeded but no coverage tap fired
(coverage already filled to 1+ for the lowest-gap dim), we copy the
seeded question into `state.taps` and null `state.selection` so:

  * the response generator sees `tap_pending` and reverts to a brief
    acknowledgment only,
  * the UI renders the question as a tap chip,
  * the `answered_by` extraction edge still gets written via the
    tap_question_ids path.

In steady phase this is a no-op — the bot keeps inlining its question.
"""

from __future__ import annotations

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.protocol import Tap
from flashback.orchestrator.state import TurnState
from flashback.orchestrator.tap_options import generate_tap_options
from flashback.phase_gate.steady_selector import PRODUCER_SOURCES

log = structlog.get_logger("flashback.orchestrator")


async def promote_seeded_to_tap(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "promote_seeded_to_tap"):
        if state.taps:
            return
        selection = state.selection
        if selection is None or selection.question_id is None or not selection.question_text:
            return
        # Producer-bank questions (P1-P5) carry the new chip surface inline,
        # not the tap-card surface. Coverage taps (P0) use their own path
        # via select_coverage_tap. See CLAUDE.md cold-start machinery + the
        # question_decisions plan. Skipping here keeps us from rendering
        # two competing skip surfaces on the same question.
        if selection.source in PRODUCER_SOURCES:
            log.info(
                "promote_seeded_to_tap.skipped",
                reason="producer_bank_uses_chip_surface",
                source=selection.source,
            )
            return

        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state
        if wm_state.taps_emitted_this_session >= 2:
            log.info("promote_seeded_to_tap.skipped", reason="session_cap")
            return
        if wm_state.user_turns_since_last_tap < 2:
            log.info(
                "promote_seeded_to_tap.skipped",
                reason="cooldown",
                user_turns_since_last_tap=wm_state.user_turns_since_last_tap,
            )
            return

        async with deps.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT phase, name, relationship FROM persons WHERE id = %s",
                    (str(state.person_id),),
                )
                row = await cur.fetchone()
        if row is None or row[0] != "starter":
            return

        person_name = str(row[1]) if row[1] else ""
        person_relationship = str(row[2]) if row[2] else None

        dimension = selection.dimension or ""
        options = await generate_tap_options(
            settings=deps.settings,
            question_text=selection.question_text,
            person_name=person_name,
            person_relationship=person_relationship,
            dimension=dimension,
        )
        state.taps = [
            Tap(
                question_id=selection.question_id,
                text=selection.question_text,
                dimension=dimension,
                options=options,
            )
        ]
        state.selection = None
        await deps.working_memory.record_tap_emitted(
            session_id=str(state.session_id),
            question_id=str(state.taps[0].question_id),
            question_text=state.taps[0].text,
        )
        log.info(
            "seeded_question.promoted_to_tap",
            question_id=str(state.taps[0].question_id),
            dimension=dimension or "<unset>",
            options_count=len(options),
        )
