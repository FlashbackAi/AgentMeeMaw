"""Per-person execution for P2/P3/P5 question producers."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from flashback.llm.errors import LLMError, LLMTimeout

from .idempotency import is_processed, mark_processed, mark_processed_empty
from .life_period import P3LifePeriodGap
from .persistence import PersistResult, persist_producer_result, push_question_embeddings
from .schema import ProducerResult
from .underdeveloped import P2Underdeveloped
from .universal import P5UniversalCoverage

log = structlog.get_logger("flashback.workers.producers.runner")


PRODUCERS_BY_TAG = {
    "P2": P2Underdeveloped,
    "P3": P3LifePeriodGap,
    "P5": P5UniversalCoverage,
}


@dataclass
class RunResult:
    skipped: bool = False
    empty: bool = False
    error: str | None = None
    persist: PersistResult | None = None

    @classmethod
    def skip(cls) -> "RunResult":
        return cls(skipped=True)

    @classmethod
    def empty_result(cls) -> "RunResult":
        return cls(empty=True)

    @classmethod
    def permanent_error(cls, error: str) -> "RunResult":
        return cls(error=error)

    @classmethod
    def from_persist(cls, persist: PersistResult) -> "RunResult":
        return cls(persist=persist)

    def summary(self) -> dict:
        if self.skipped:
            return {"skipped": True}
        if self.empty:
            return {"skipped": False, "empty": True, "questions_written": 0}
        if self.error is not None:
            return {
                "skipped": False,
                "empty": False,
                "permanent_error": self.error,
                "questions_written": 0,
            }
        assert self.persist is not None
        return {"skipped": False, "empty": False, **self.persist.summary()}


async def run_once(
    *,
    db_pool,
    embedding_sender,
    settings,
    producer_tag: str,
    person_id: UUID,
    idempotency_key: str,
    embedding_model: str,
    embedding_model_version: str,
) -> RunResult:
    """Run one producer for one person and persist its output."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            if is_processed(cur, idempotency_key):
                log.info(
                    "producer.skipped_already_processed",
                    idempotency_key=idempotency_key,
                    person_id=str(person_id),
                    producer=producer_tag,
                )
                return RunResult.skip()

    producer_cls = PRODUCERS_BY_TAG.get(producer_tag)
    if producer_cls is None:
        raise ValueError(f"unknown producer: {producer_tag}")

    producer = producer_cls()
    try:
        produced: ProducerResult = await producer.produce(
            db_pool, person_id, settings
        )
    except LLMTimeout:
        raise
    except LLMError as exc:
        log.error(
            "producer.llm_permanent_error",
            producer=producer_tag,
            person_id=str(person_id),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        mark_processed_empty(
            db_pool,
            idempotency_key=idempotency_key,
            person_id=str(person_id),
            producer=producer_tag,
        )
        return RunResult.permanent_error(str(exc))

    if not produced.questions:
        log.info(
            "producer.no_questions",
            producer=producer_tag,
            person_id=str(person_id),
            reasoning=produced.overall_reasoning,
        )
        mark_processed_empty(
            db_pool,
            idempotency_key=idempotency_key,
            person_id=str(person_id),
            producer=producer_tag,
        )
        return RunResult.empty_result()

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                persist = persist_producer_result(cur, result=produced)
                mark_processed(
                    cur,
                    idempotency_key=idempotency_key,
                    person_id=str(person_id),
                    producer=producer_tag,
                    questions_written=persist.questions_written,
                )

    push_question_embeddings(
        embedding_sender=embedding_sender,
        result=produced,
        question_ids=persist.question_ids,
        embedding_model=embedding_model,
        embedding_model_version=embedding_model_version,
    )
    log.info(
        "producer.persisted",
        producer=producer_tag,
        person_id=str(person_id),
        idempotency_key=idempotency_key,
        **persist.summary(),
    )
    return RunResult.from_persist(persist)

