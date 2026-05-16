"""Retrieval Service wiring.

Retrieval calls are gated on ``effective_intent``:

- ``recall``  → ``search_moments`` + ``search_entities`` (vector, two Voyage calls)
- ``switch``  → ``get_entities`` + ``get_threads`` (plain SQL, no embedding)
- ``pivot``   → ``search_entities`` + ``get_entities`` (one Voyage call;
                semantic hits ranked first, full catalog appended for
                deterministic lookup)
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
            elif intent == "pivot":
                semantic_hits, catalog = await asyncio.gather(
                    deps.retrieval.search_entities(
                        query=state.user_message,
                        person_id=state.person_id,
                    ),
                    deps.retrieval.get_entities(state.person_id),
                )
                state.related_entities = _merge_entity_lists(semantic_hits, catalog)
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


def _merge_entity_lists(semantic_hits, catalog):
    """Combine semantic hits first (best matches), then catalog by id-novelty.

    Semantic hits already carry similarity scores; their order is the
    Voyage ranking. The remainder of the catalog is appended so the
    response generator still sees every active entity for deterministic
    name-based resolution — matching the ``pivot`` contract that fuses
    descriptive (semantic) and named (catalog) references.
    """
    seen = set()
    merged = []
    for entity in semantic_hits:
        eid = getattr(entity, "id", None)
        if eid is None or eid in seen:
            continue
        seen.add(eid)
        merged.append(entity)
    for entity in catalog:
        eid = getattr(entity, "id", None)
        if eid is None or eid in seen:
            continue
        seen.add(eid)
        merged.append(entity)
    return merged
