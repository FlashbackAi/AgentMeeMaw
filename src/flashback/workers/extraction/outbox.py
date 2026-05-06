"""Durable extraction fan-out via a Postgres outbox.

Extraction graph writes and outbox inserts happen in the same
transaction. SQS sends happen after commit by draining this table, so a
process crash cannot strand moments without embedding/artifact jobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import structlog
from psycopg.types.json import Json

from .persistence import PersistenceResult
from .schema import ExtractionResult
from .thread_trigger import ThreadTriggerStatus

log = structlog.get_logger("flashback.workers.extraction.outbox")

OutboxJobType = Literal["embedding", "artifact", "thread_detector"]


@dataclass(frozen=True)
class OutboxJob:
    id: str
    job_type: OutboxJobType
    payload: dict[str, Any]
    attempts: int


def enqueue_extraction_fanout(
    cursor,
    *,
    source_sqs_message_id: str,
    person_id: str,
    extraction: ExtractionResult,
    persistence_result: PersistenceResult,
    embedding_model: str,
    embedding_model_version: str,
) -> int:
    """Insert embedding/artifact fan-out jobs in the caller's transaction."""
    jobs: list[tuple[OutboxJobType, dict[str, Any]]] = []
    jobs.extend(
        _embedding_jobs(
            extraction=extraction,
            persistence_result=persistence_result,
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )
    )
    jobs.extend(
        _artifact_jobs(
            person_id=person_id,
            extraction=extraction,
            persistence_result=persistence_result,
        )
    )
    for job_type, payload in jobs:
        cursor.execute(
            """
            INSERT INTO extraction_outbox
                  (source_sqs_message_id, person_id, job_type, payload)
            VALUES (%s,                    %s,        %s,       %s)
            """,
            (source_sqs_message_id, person_id, job_type, Json(payload)),
        )
    return len(jobs)


def enqueue_thread_detector_trigger_if_due(
    cursor,
    *,
    source_sqs_message_id: str,
    person_id: str,
    cadence: int = 15,
) -> ThreadTriggerStatus:
    """Check the thread detector cadence in-transaction and enqueue if due."""
    cursor.execute(
        """
        SELECT
            (SELECT count(*) FROM active_moments WHERE person_id = %(pid)s)
                AS active_count,
            p.moments_at_last_thread_run AS last_count
        FROM persons p
        WHERE p.id = %(pid)s
        """,
        {"pid": person_id},
    )
    row = cursor.fetchone()
    if row is None:
        return ThreadTriggerStatus(0, 0, 0, False)

    active_count, last_count = int(row[0]), int(row[1])
    delta = active_count - last_count
    would_trigger = active_count >= cadence and delta >= cadence
    if would_trigger:
        cursor.execute(
            """
            INSERT INTO extraction_outbox
                  (source_sqs_message_id, person_id, job_type, payload)
            VALUES (%s,                    %s,        'thread_detector', %s)
            """,
            (
                source_sqs_message_id,
                person_id,
                Json(
                    {
                        "person_id": person_id,
                        "active_count_at_trigger": active_count,
                        "last_count_at_trigger": last_count,
                    }
                ),
            ),
        )
    return ThreadTriggerStatus(
        active_count=active_count,
        last_count=last_count,
        delta=delta,
        would_trigger=would_trigger,
        pushed=False,
    )


def drain_extraction_outbox(
    db_pool,
    *,
    embedding_sender,
    artifact_sender,
    thread_detector_sender,
    source_sqs_message_id: str | None = None,
    limit: int = 100,
) -> int:
    """Reserve and send pending outbox jobs. Returns sent count."""
    sent = 0
    while sent < limit:
        jobs = _reserve_jobs(
            db_pool,
            source_sqs_message_id=source_sqs_message_id,
            limit=limit - sent,
        )
        if not jobs:
            break
        for job in jobs:
            try:
                _send_job(
                    job,
                    embedding_sender=embedding_sender,
                    artifact_sender=artifact_sender,
                    thread_detector_sender=thread_detector_sender,
                )
            except Exception as exc:  # noqa: BLE001
                _mark_failed(db_pool, job=job, error=str(exc))
                log.warning(
                    "extraction_outbox.send_failed",
                    outbox_id=job.id,
                    job_type=job.job_type,
                    attempts=job.attempts,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                continue
            _mark_sent(db_pool, job.id)
            sent += 1
    if sent:
        log.info("extraction_outbox.drained", count=sent)
    return sent


def _reserve_jobs(
    db_pool, *, source_sqs_message_id: str | None, limit: int
) -> list[OutboxJob]:
    if limit <= 0:
        return []
    source_filter = (
        "AND source_sqs_message_id = %(source)s"
        if source_sqs_message_id is not None
        else ""
    )
    sql = f"""
        WITH picked AS (
            SELECT id
              FROM extraction_outbox
             WHERE (
                    (status = 'pending' AND available_at <= now())
                    OR (
                        status = 'in_progress'
                        AND updated_at < now() - interval '10 minutes'
                    )
               )
               {source_filter}
             ORDER BY created_at
             LIMIT %(limit)s
             FOR UPDATE SKIP LOCKED
        )
        UPDATE extraction_outbox o
           SET status = 'in_progress',
               attempts = attempts + 1,
               updated_at = now()
          FROM picked
         WHERE o.id = picked.id
         RETURNING o.id::text, o.job_type, o.payload, o.attempts
    """
    params = {"limit": limit, "source": source_sqs_message_id}
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    return [
        OutboxJob(
            id=row[0],
            job_type=row[1],
            payload=dict(row[2]),
            attempts=int(row[3]),
        )
        for row in rows
    ]


def _send_job(
    job: OutboxJob,
    *,
    embedding_sender,
    artifact_sender,
    thread_detector_sender,
) -> None:
    payload = job.payload
    if job.job_type == "embedding":
        embedding_sender.send(**payload)
        return
    if job.job_type == "artifact":
        artifact_sender.send(**payload)
        return
    if job.job_type == "thread_detector":
        thread_detector_sender.send(**payload)
        return
    raise ValueError(f"unknown extraction outbox job_type: {job.job_type!r}")


def _mark_sent(db_pool, outbox_id: str) -> None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE extraction_outbox
                   SET status = 'sent',
                       sent_at = now(),
                       updated_at = now()
                 WHERE id = %s
                """,
                (outbox_id,),
            )
            conn.commit()


def _mark_failed(db_pool, *, job: OutboxJob, error: str) -> None:
    delay_seconds = min(300, 2 ** min(job.attempts, 8))
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE extraction_outbox
                   SET status = 'pending',
                       last_error = %s,
                       available_at = now() + (%s * interval '1 second'),
                       updated_at = now()
                 WHERE id = %s
                """,
                (error[:2000], delay_seconds, job.id),
            )
            conn.commit()


def _embedding_jobs(
    *,
    extraction: ExtractionResult,
    persistence_result: PersistenceResult,
    embedding_model: str,
    embedding_model_version: str,
) -> list[tuple[OutboxJobType, dict[str, Any]]]:
    jobs: list[tuple[OutboxJobType, dict[str, Any]]] = []
    if len(extraction.moments) != len(persistence_result.moment_ids):
        raise ValueError("moments and moment_ids must have matching lengths")
    for moment, mid in zip(
        extraction.moments, persistence_result.moment_ids, strict=True
    ):
        if moment.narrative:
            jobs.append(
                (
                    "embedding",
                    {
                        "record_type": "moment",
                        "record_id": mid,
                        "source_text": moment.narrative,
                        "embedding_model": embedding_model,
                        "embedding_model_version": embedding_model_version,
                    },
                )
            )

    if len(persistence_result.surviving_entities) != len(
        persistence_result.entity_ids
    ):
        raise ValueError("surviving_entities and entity_ids must match")
    for entity, eid in zip(
        persistence_result.surviving_entities,
        persistence_result.entity_ids,
        strict=True,
    ):
        if entity.description:
            jobs.append(
                (
                    "embedding",
                    {
                        "record_type": "entity",
                        "record_id": eid,
                        "source_text": entity.description,
                        "embedding_model": embedding_model,
                        "embedding_model_version": embedding_model_version,
                    },
                )
            )

    if len(extraction.traits) != len(persistence_result.trait_ids):
        raise ValueError("traits and trait_ids must have matching lengths")
    for trait, tid in zip(extraction.traits, persistence_result.trait_ids, strict=True):
        source_text = trait.name
        if trait.description:
            source_text = f"{trait.name}, {trait.description}"
        jobs.append(
            (
                "embedding",
                {
                    "record_type": "trait",
                    "record_id": tid,
                    "source_text": source_text,
                    "embedding_model": embedding_model,
                    "embedding_model_version": embedding_model_version,
                },
            )
        )

    if len(extraction.dropped_references) != len(persistence_result.question_ids):
        raise ValueError("dropped_references and question_ids must match")
    for dr, qid in zip(
        extraction.dropped_references, persistence_result.question_ids, strict=True
    ):
        jobs.append(
            (
                "embedding",
                {
                    "record_type": "question",
                    "record_id": qid,
                    "source_text": dr.question_text,
                    "embedding_model": embedding_model,
                    "embedding_model_version": embedding_model_version,
                },
            )
        )
    return jobs


def _artifact_jobs(
    *,
    person_id: str,
    extraction: ExtractionResult,
    persistence_result: PersistenceResult,
) -> list[tuple[OutboxJobType, dict[str, Any]]]:
    jobs: list[tuple[OutboxJobType, dict[str, Any]]] = []
    if len(extraction.moments) != len(persistence_result.moment_ids):
        raise ValueError("moments and moment_ids must have matching lengths")
    for moment, mid in zip(
        extraction.moments, persistence_result.moment_ids, strict=True
    ):
        if moment.generation_prompt:
            jobs.append(
                (
                    "artifact",
                    {
                        "record_type": "moment",
                        "record_id": mid,
                        "person_id": person_id,
                        "artifact_kind": "video",
                        "generation_prompt": moment.generation_prompt,
                    },
                )
            )

    if len(persistence_result.surviving_entities) != len(
        persistence_result.entity_ids
    ):
        raise ValueError("surviving_entities and entity_ids must match")
    for entity, eid in zip(
        persistence_result.surviving_entities,
        persistence_result.entity_ids,
        strict=True,
    ):
        if entity.generation_prompt:
            jobs.append(
                (
                    "artifact",
                    {
                        "record_type": "entity",
                        "record_id": eid,
                        "person_id": person_id,
                        "artifact_kind": "image",
                        "generation_prompt": entity.generation_prompt,
                    },
                )
            )
    return jobs
