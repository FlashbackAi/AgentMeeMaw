"""Retrieval Service wiring.

Retrieval calls are gated on ``effective_intent``:

- ``recall``  → ``search_moments`` + ``search_entities`` (vector, two Voyage calls)
- ``switch``  → ``get_entities`` + ``get_threads`` (plain SQL, no embedding)
- ``clarify`` / ``deepen`` / ``story`` → no retrieval

The classifier's ``OUTCOMES`` section documents this contract; the gate here
is the implementation half of it.
"""

from __future__ import annotations

import asyncio

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

        intent = state.effective_intent
        try:
            if intent == "recall":
                state.related_moments, state.related_entities = await asyncio.gather(
                    deps.retrieval.search_moments(
                        query=state.user_message,
                        person_id=state.person_id,
                    ),
                    deps.retrieval.search_entities(
                        query=state.user_message,
                        person_id=state.person_id,
                    ),
                )
            elif intent == "switch":
                state.related_entities, state.related_threads = await asyncio.gather(
                    deps.retrieval.get_entities(state.person_id),
                    deps.retrieval.get_threads(state.person_id),
                )
            else:
                log.info("retrieval.skipped", reason="intent_no_retrieval", intent=intent)
                return
        except Exception as exc:
            raise LLMError(f"retrieval failed: {exc}") from exc

        log.info(
            "retrieval.called",
            intent=intent,
            n_moments=len(state.related_moments),
            n_entities=len(state.related_entities),
            n_threads=len(state.related_threads),
        )
