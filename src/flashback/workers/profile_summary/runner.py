"""Per-person execution for the Profile Summary Generator.

The :func:`run_once` entry point is shared by:

* The SQS drain loop (worker.py), which calls it with the SQS
  ``MessageId`` as ``idempotency_key``.
* The CLI ``run-once --person-id <uuid>`` subcommand, which calls it
  with a ``runonce-{person_id}-{ms}`` synthetic key (best-effort
  idempotency for ad-hoc testing).

Sequence:

  1. Idempotency check (read-only). If already processed, return
     :class:`RunResult.skipped`.
  2. Build context (DB read, code-derived time period).
  3. Empty-legacy short-circuit: if no traits, threads, OR entities,
     skip the LLM call entirely. Still write the idempotency row so a
     redelivery doesn't repeat the no-op.
  4. Single LLM call (raises ``LLMTimeout`` / ``LLMError`` on failure).
  5. Per-person transaction: UPDATE persons.profile_summary +
     INSERT processed_profile_summaries.

Failure modes propagate to the caller as exceptions; :mod:`worker`
maps them to ack / no-ack semantics.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from .context import build_context
from .idempotency import is_processed, mark_processed_empty
from .persistence import PersistResult, persist_summary
from .summary_llm import SummaryLLMConfig, generate_summary

log = structlog.get_logger("flashback.workers.profile_summary.runner")


@dataclass
class RunResult:
    """Outcome of one ``run_once`` invocation."""

    skipped: bool = False
    empty: bool = False
    persist: PersistResult | None = None

    @classmethod
    def skip(cls) -> "RunResult":
        return cls(skipped=True, empty=False, persist=None)

    @classmethod
    def empty_legacy(cls) -> "RunResult":
        return cls(skipped=False, empty=True, persist=None)

    @classmethod
    def from_persist(cls, persist: PersistResult) -> "RunResult":
        return cls(skipped=False, empty=False, persist=persist)

    def summary(self) -> dict:
        if self.skipped:
            return {"skipped": True}
        if self.empty:
            return {"skipped": False, "empty_legacy": True, "summary_chars": 0}
        assert self.persist is not None
        return {"skipped": False, **self.persist.summary()}


def run_once(
    *,
    db_pool,
    summary_cfg: SummaryLLMConfig,
    settings,
    person_id: str,
    idempotency_key: str,
    top_traits_max: int,
    top_threads_max: int,
    top_entities_max: int,
) -> RunResult:
    """Generate the profile summary for one person, end to end."""
    # 1. Idempotency check.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            if is_processed(cur, idempotency_key):
                log.info(
                    "profile_summary.skipped_already_processed",
                    idempotency_key=idempotency_key,
                    person_id=person_id,
                )
                return RunResult.skip()

    # 2. Context.
    context = build_context(
        db_pool,
        person_id=person_id,
        top_traits_max=top_traits_max,
        top_threads_max=top_threads_max,
        top_entities_max=top_entities_max,
    )

    # 3. Empty-legacy short-circuit.
    if not context.traits and not context.threads and not context.entities:
        log.info(
            "profile_summary.empty_legacy_skip",
            person_id=person_id,
            idempotency_key=idempotency_key,
        )
        mark_processed_empty(
            db_pool,
            idempotency_key=idempotency_key,
            person_id=person_id,
        )
        return RunResult.empty_legacy()

    # 4. LLM call (may raise LLMTimeout / LLMError).
    summary_text = generate_summary(
        cfg=summary_cfg,
        settings=settings,
        context=context,
    )

    # 5. Per-person transaction.
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                persist = persist_summary(
                    cur,
                    person_id=person_id,
                    summary_text=summary_text,
                    idempotency_key=idempotency_key,
                )

    log.info(
        "profile_summary.persisted",
        person_id=person_id,
        idempotency_key=idempotency_key,
        **persist.summary(),
    )
    return RunResult.from_persist(persist)
