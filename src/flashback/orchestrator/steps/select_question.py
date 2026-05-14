"""Phase Gate wiring for switch-intent turns."""

from __future__ import annotations

from uuid import UUID

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState

log = structlog.get_logger("flashback.orchestrator")


async def select_question(state: TurnState, deps: OrchestratorDeps) -> None:
    """Select the next seeded question and record it in the WM register.

    The Working Memory ``asked`` LIST is the session-scoped dedup
    register. Both starter and steady selection consume it to avoid
    re-asking a question within the same session, regardless of
    whether the prior turn produced an extracted moment. After a fresh
    selection succeeds, this step appends the chosen question id so
    the next ``switch`` turn excludes it too.
    """
    with timed_step(log, "select_question"):
        if deps.phase_gate is None:
            log.info("phase_gate.skipped", reason="not_configured")
            return
        recently_asked_ids: list[UUID] = []
        if deps.working_memory is not None:
            raw_ids = await deps.working_memory.get_recently_asked_question_ids(
                str(state.session_id)
            )
            recently_asked_ids = [UUID(qid) for qid in raw_ids if qid]
        state.selection = await deps.phase_gate.select_next_question(
            person_id=state.person_id,
            session_id=state.session_id,
            recently_asked_ids=recently_asked_ids,
        )
        if (
            deps.working_memory is not None
            and state.selection is not None
            and state.selection.question_id is not None
        ):
            await deps.working_memory.append_asked_question(
                session_id=str(state.session_id),
                question_id=str(state.selection.question_id),
            )
        log.info(
            "phase_gate.selected",
            phase=state.selection.phase,
            question_id=(
                str(state.selection.question_id)
                if state.selection.question_id is not None
                else None
            ),
            source=state.selection.source,
            rationale=state.selection.rationale,
            recently_asked_n=len(recently_asked_ids),
        )
