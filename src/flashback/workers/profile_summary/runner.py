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
     skip both LLM calls. Still write the idempotency row.
  4. Prose-summary LLM call (raises ``LLMTimeout`` / ``LLMError``).
  5. Per-person transaction: UPDATE persons.profile_summary +
     INSERT processed_profile_summaries.
  6. Profile-fact extraction (best-effort; failures don't roll back
     the prose summary). For each high-confidence fact, upsert to
     ``profile_facts`` and push an embedding job.

Failure modes for steps 4-5 propagate to the caller as exceptions;
:mod:`worker` maps them to ack / no-ack semantics. Step 6 swallows its
own LLM errors — facts are an enhancement, not load-bearing for the
profile page.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from flashback.llm.errors import LLMError
from flashback.profile_facts.extraction import (
    FactExtractionConfig,
    extract_facts,
)
from flashback.profile_facts.repository import upsert_fact

from .context import build_context, render_context
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
    facts_extracted: int = 0
    facts_upserted: int = 0

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
        return {
            "skipped": False,
            **self.persist.summary(),
            "facts_extracted": self.facts_extracted,
            "facts_upserted": self.facts_upserted,
        }


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
    fact_extraction_cfg: FactExtractionConfig | None = None,
    embedding_sender=None,
    embedding_model: str | None = None,
    embedding_model_version: str | None = None,
) -> RunResult:
    """Generate the profile summary + extract profile facts for one person.

    The fact-extraction step is opt-in: it runs only when
    ``fact_extraction_cfg`` and ``embedding_sender`` are both supplied.
    The CLI run-once path can pass them or omit them — omission is
    backwards-compatible with the prior summary-only behavior.
    """
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

    # 4. Summary LLM call.
    summary_text = generate_summary(
        cfg=summary_cfg,
        settings=settings,
        context=context,
    )

    # 5. Per-person transaction (summary only — fact extraction is
    # outside this transaction so an LLM failure on facts doesn't
    # roll back the summary).
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

    # 6. Profile-fact extraction (best-effort).
    facts_extracted = 0
    facts_upserted = 0
    if fact_extraction_cfg is not None and embedding_sender is not None:
        if embedding_model is None or embedding_model_version is None:
            raise ValueError(
                "embedding_model and embedding_model_version are required "
                "when fact_extraction_cfg is provided"
            )
        rendered = render_context(context)
        try:
            extracted = extract_facts(
                cfg=fact_extraction_cfg,
                settings=settings,
                rendered_context=rendered,
            )
        except LLMError as exc:
            log.warning(
                "profile_facts.extraction_failed_soft",
                person_id=person_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            extracted = []

        facts_extracted = len(extracted)
        for fact in extracted:
            if fact.confidence != "high":
                log.info(
                    "profile_facts.dropped_low_confidence",
                    person_id=person_id,
                    fact_key=fact.fact_key,
                    confidence=fact.confidence,
                )
                continue

            try:
                with db_pool.connection() as conn:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            result = upsert_fact(
                                cur,
                                person_id=person_id,
                                fact_key=fact.fact_key,
                                question_text=fact.question_text,
                                answer_text=fact.answer_text,
                                source="starter_extraction",
                                push_embedding=embedding_sender,
                                embedding_model=embedding_model,
                                embedding_model_version=embedding_model_version,
                            )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "profile_facts.upsert_failed_soft",
                    person_id=person_id,
                    fact_key=fact.fact_key,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                continue

            if not result.skipped and not result.cap_reached:
                facts_upserted += 1

    return RunResult(
        skipped=False,
        empty=False,
        persist=persist,
        facts_extracted=facts_extracted,
        facts_upserted=facts_upserted,
    )
