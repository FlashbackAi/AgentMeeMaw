"""
The embedding worker drain loop.

The worker is a long-running process. Each iteration:

  1. Long-poll SQS for up to N messages.
  2. Group messages by ``(embedding_model, embedding_model_version)``.
     Almost always one group, but if a model upgrade is in flight the
     queue can carry mixed identities for a short window.
  3. For each group, do **one** Voyage batch call.
  4. For each (message, vector) pair, run the version-guarded UPDATE.
  5. Ack each message individually based on its UPDATE outcome.

Failure handling:

  * Voyage failure -> do **not** ack any message in the failing group.
    SQS visibility timeout will redeliver. The worker logs and moves on.
  * DB UPDATE that returns 0 rows -> ack anyway. Zero rows means the
    work is no longer needed (row gone, status no longer 'active', or
    embedding model already moved on). Retrying would be wrong.
  * DB UPDATE that raises -> do **not** ack. Let SQS redeliver.

Invariants honoured (CLAUDE.md s4):

  * #3 - never mix vectors across models. Enforced by the version
    guard: the UPDATE only touches a row whose existing model identity
    is NULL (first embed) or matches the message's identity exactly.
  * #4 - never embed inline. The worker is the only writer of vector
    columns; it is queue-driven only. The backfill CLI never writes
    vectors itself - it just publishes jobs back onto the queue.
"""

from __future__ import annotations

import logging
import signal
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from psycopg import errors as psycopg_errors

from flashback.db.embedding_targets import EMBEDDING_TARGETS, get_target

from .sqs_client import EmbeddingMessage, SQSClient
from .voyage_client import VoyageClient, VoyageError

log = logging.getLogger(__name__)


class _PoolLike(Protocol):
    def connection(self): ...


# Outcomes of a per-row UPDATE attempt. Only ROW_UPDATED and
# GUARD_SKIPPED ack. DB_ERROR leaves the message in flight so SQS
# redrives it.
@dataclass(frozen=True)
class UpdateResult:
    acked: bool
    rows_affected: int
    note: str


_GUARD_SKIPPED = UpdateResult(acked=True, rows_affected=0, note="guard_skipped")
_ROW_UPDATED = UpdateResult(acked=True, rows_affected=1, note="updated")
_DB_ERROR = UpdateResult(acked=False, rows_affected=0, note="db_error")


def _build_update_sql(table: str, vector_column: str) -> str:
    """
    The version-guarded UPDATE. Identical shape across tables; only the
    table and vector column vary.

    The guard:

        WHERE id = $record_id
          AND status = 'active'
          AND (
            embedding_model IS NULL
            OR (embedding_model = $model AND embedding_model_version = $version)
          )

    means we update first-time-ever (NULL) **or** same-model re-embed,
    and never overwrite a vector that's already on a different model.

    persons is not in EMBEDDING_TARGETS, so we never need to special-case
    a status-less table here.
    """
    return f"""
        UPDATE {table}
           SET {vector_column}         = %(vector)s,
               embedding_model         = %(model)s,
               embedding_model_version = %(version)s
         WHERE id = %(record_id)s
           AND status = 'active'
           AND (
                embedding_model IS NULL
                OR (
                    embedding_model = %(model)s
                    AND embedding_model_version = %(version)s
                )
           )
        RETURNING id
    """


def _group_by_model(
    messages: list[EmbeddingMessage],
) -> dict[tuple[str, str], list[EmbeddingMessage]]:
    groups: dict[tuple[str, str], list[EmbeddingMessage]] = defaultdict(list)
    for m in messages:
        groups[(m.embedding_model, m.embedding_model_version)].append(m)
    return groups


def _apply_update(
    pool: _PoolLike, msg: EmbeddingMessage, vector: list[float]
) -> UpdateResult:
    target = get_target(msg.record_type)
    sql = _build_update_sql(target.table, target.vector_column)
    params = {
        "vector": vector,
        "model": msg.embedding_model,
        "version": msg.embedding_model_version,
        "record_id": msg.record_id,
    }
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                conn.commit()
    except psycopg_errors.Error as exc:
        log.error(
            "embedding.update_failed",
            extra={
                "record_type": msg.record_type,
                "record_id": msg.record_id,
                "error": str(exc),
            },
        )
        return _DB_ERROR

    if row is None:
        log.info(
            "embedding.update_skipped",
            extra={
                "record_type": msg.record_type,
                "record_id": msg.record_id,
                "reason": "guard_skipped",
            },
        )
        return _GUARD_SKIPPED

    log.info(
        "embedding.row_updated",
        extra={
            "record_type": msg.record_type,
            "record_id": msg.record_id,
            "model": msg.embedding_model,
            "version": msg.embedding_model_version,
        },
    )
    return _ROW_UPDATED


def process_batch(
    messages: list[EmbeddingMessage],
    *,
    pool: _PoolLike,
    voyage: VoyageClient,
    sqs: SQSClient,
) -> None:
    """
    Process one SQS receive's worth of messages.

    Exposed at module level so the test suite can drive a single batch
    without spinning the long-poll loop.
    """
    if not messages:
        return

    for unknown in (m for m in messages if m.record_type not in EMBEDDING_TARGETS):
        log.error(
            "embedding.unknown_record_type",
            extra={"record_type": unknown.record_type, "record_id": unknown.record_id},
        )
        sqs.delete(unknown.receipt_handle)
    messages = [m for m in messages if m.record_type in EMBEDDING_TARGETS]
    if not messages:
        return

    for (model, _version), group in _group_by_model(messages).items():
        try:
            vectors = voyage.embed_batch(
                [m.source_text for m in group], model=model
            )
        except VoyageError as exc:
            log.error(
                "embedding.voyage_failed",
                extra={"model": model, "batch_size": len(group), "error": str(exc)},
            )
            continue

        for msg, vector in zip(group, vectors, strict=True):
            result = _apply_update(pool, msg, vector)
            if result.acked:
                sqs.delete(msg.receipt_handle)


class _StopSignal:
    """SIGINT/SIGTERM handler that flips a flag the loop checks each tick."""

    def __init__(self) -> None:
        self.requested = False

    def install(self) -> None:
        signal.signal(signal.SIGINT, self._handle)
        try:
            signal.signal(signal.SIGTERM, self._handle)
        except (AttributeError, ValueError):
            # SIGTERM unavailable on Windows main-thread signal API.
            pass

    def _handle(self, *_args) -> None:
        self.requested = True


def run_forever(
    *,
    pool: _PoolLike,
    voyage: VoyageClient,
    sqs: SQSClient,
    max_messages: int = 10,
    wait_seconds: int = 20,
    stop: _StopSignal | None = None,
) -> None:
    """
    Long-running drain loop. Returns only on SIGINT/SIGTERM.

    SQS long-polling provides natural backpressure: an idle queue
    blocks for ``wait_seconds`` per call, so an empty queue is cheap.
    """
    stop = stop or _StopSignal()
    stop.install()

    log.info(
        "embedding.worker_started",
        extra={"max_messages": max_messages, "wait_seconds": wait_seconds},
    )
    while not stop.requested:
        messages = sqs.receive(
            max_messages=max_messages, wait_seconds=wait_seconds
        )
        if not messages:
            continue
        process_batch(messages, pool=pool, voyage=voyage, sqs=sqs)
    log.info("embedding.worker_stopped")
