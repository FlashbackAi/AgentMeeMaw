"""Pre-retrieval step: scan the user message for known entity mentions.

Runs every turn, regardless of intent. Uses a Valkey-cached list of
the person's active entities to do deterministic word-boundary matching
on ``state.user_message``. Hits are loaded by id from Postgres and
attached to ``state.mentioned_entities``; if two distinct entities
collide on the same surface form, ``state.ambiguous_mention`` is set
so the response generator can disambiguate on the next turn.

No Voyage call, no semantic search. This sits orthogonally to the
intent-gated retrieval matrix in ``retrieve.py``.
"""

from __future__ import annotations

import structlog

from flashback.entity_mention.matcher import find_entity_mentions
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState

log = structlog.get_logger("flashback.orchestrator")


async def scan_entity_mentions(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "entity_mention_scan"):
        if deps.entity_name_cache is None or deps.retrieval is None:
            log.info("entity_mention_scan.skipped", reason="not_configured")
            return

        try:
            entries = await deps.entity_name_cache.get(state.person_id)
        except Exception as exc:
            log.warning("entity_mention_scan.cache_read_failed", error=str(exc))
            return

        if not entries:
            log.info("entity_mention_scan.empty_cache", person_id=str(state.person_id))
            return

        matches, ambiguous = find_entity_mentions(state.user_message, entries)
        if not matches:
            log.info("entity_mention_scan.no_match", scanned=len(entries))
            return

        matched_ids = [m.entity_id for m in matches]
        try:
            state.mentioned_entities = await deps.retrieval.get_entities_by_ids(
                state.person_id, matched_ids
            )
        except Exception as exc:
            log.warning("entity_mention_scan.fetch_failed", error=str(exc))
            return

        state.ambiguous_mention = ambiguous
        log.info(
            "entity_mention_scan.matched",
            n=len(state.mentioned_entities),
            ambiguous=ambiguous,
            surface_forms=[m.matched_text for m in matches],
        )
