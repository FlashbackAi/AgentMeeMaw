"""Retrieval Service wiring."""

from __future__ import annotations

import structlog

from flashback.llm.errors import LLMError
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState

log = structlog.get_logger("flashback.orchestrator")


async def retrieve(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "retrieve"):
        if deps.retrieval is None:
            log.info("retrieval.skipped", reason="not_configured")
            return
        try:
            state.related_moments = await deps.retrieval.search_moments(
                query=state.user_message,
                person_id=state.person_id,
            )
            if state.effective_intent == "switch":
                state.related_entities = await deps.retrieval.get_entities(
                    state.person_id
                )
                state.related_threads = await deps.retrieval.get_threads(
                    state.person_id
                )
        except Exception as exc:
            raise LLMError(f"retrieval failed: {exc}") from exc

        log.info(
            "retrieval.called",
            intent=state.effective_intent,
            n_moments=len(state.related_moments),
            n_entities=len(state.related_entities),
            n_threads=len(state.related_threads),
        )
