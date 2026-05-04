"""Phase Gate wiring for switch-intent turns."""

from __future__ import annotations

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState

log = structlog.get_logger("flashback.orchestrator")


async def select_question(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "select_question"):
        if deps.phase_gate is None:
            log.info("phase_gate.skipped", reason="not_configured")
            return
        state.selection = await deps.phase_gate.select_next_question(
            person_id=state.person_id,
            session_id=state.session_id,
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
        )
