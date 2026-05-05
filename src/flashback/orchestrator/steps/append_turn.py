"""Append the inbound user turn to Working Memory."""

from __future__ import annotations

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState

log = structlog.get_logger("flashback.orchestrator")


async def append_user_turn(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(
        log,
        "append_user_turn",
        role="user",
        message_length=len(state.user_message),
    ):
        await deps.working_memory.append_turn(
            session_id=str(state.session_id),
            role="user",
            content=state.user_message,
            timestamp=state.started_at,
        )
        await deps.working_memory.increment_user_turns_since_segment_check(
            str(state.session_id),
        )
