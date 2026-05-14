"""Append assistant response and question-tracking signals."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState

log = structlog.get_logger("flashback.orchestrator")


async def append_assistant(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "append_assistant"):
        if state.response is None:
            raise RuntimeError("generate_response did not populate state.response")
        metadata: dict[str, object] = {}
        if state.selection and state.selection.question_id is not None:
            metadata["selected_question_id"] = str(state.selection.question_id)
        if state.taps:
            metadata["tap_question_ids"] = [
                str(tap.question_id) for tap in state.taps
            ]
        await deps.working_memory.append_turn(
            session_id=str(state.session_id),
            role="assistant",
            content=state.response.text,
            timestamp=datetime.now(timezone.utc),
            metadata=metadata,
        )
        if state.selection and state.selection.question_id is not None:
            question_id = str(state.selection.question_id)
            await deps.working_memory.append_asked_question(
                str(state.session_id),
                question_id,
            )
            await deps.working_memory.set_seeded_question(
                str(state.session_id),
                question_id,
            )
