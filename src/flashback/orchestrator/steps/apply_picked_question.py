"""Seed a session opener from an explicitly-picked feed question.

When the caller passes ``question_id`` in ``session_metadata`` (the
contributor tapped a question in the feed), load that active,
person-scoped question and set ``state.selection`` so the opener anchors
on it. This bypasses the selectors and any cooldown/recency dedup — an
explicit pick is always honored exactly.

Runs after ``select_starter_question`` so an explicit pick overrides an
auto-selected starter question, in both starter and steady phase. Never
raises: a bad id degrades to the normal opener.
"""

from __future__ import annotations

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import SessionStartState
from flashback.phase_gate.schema import SelectionResult

log = structlog.get_logger("flashback.orchestrator.apply_picked_question")

_SELECT_PICKED_QUESTION = """
SELECT id, text, source
FROM active_questions
WHERE id = %(question_id)s
  AND person_id = %(person_id)s
"""


async def apply_picked_question(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> None:
    raw_question_id = state.session_metadata.get("question_id")
    if not raw_question_id:
        return

    with timed_step(log, "apply_picked_question"):
        try:
            async with deps.db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        _SELECT_PICKED_QUESTION,
                        {
                            "question_id": str(raw_question_id),
                            "person_id": str(state.person_id),
                        },
                    )
                    row = await cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "picked_question.lookup_failed",
                question_id=str(raw_question_id),
                error=type(exc).__name__,
            )
            return

        if row is None:
            log.warning(
                "picked_question.not_found",
                question_id=str(raw_question_id),
                person_id=str(state.person_id),
            )
            return

        question_id, text, source = row
        phase = state.person_phase if state.person_phase in ("starter", "steady") \
            else "steady"
        state.selection = SelectionResult(
            phase=phase,
            question_id=question_id,
            question_text=text,
            source=source,
            rationale="explicit feed pick",
        )
        log.info(
            "picked_question.selected",
            question_id=str(question_id),
            source=source,
        )
