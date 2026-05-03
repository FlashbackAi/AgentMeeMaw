"""
Backfill CLI logic.

The backfill command scans Postgres for active rows that still have a
NULL vector and publishes one embedding job per row to SQS. The
worker - which is the only writer of vector columns - picks them up
the same way it picks up live writes from the Extraction Worker, the
Thread Detector, etc.

Two intended uses:

  1. **First-time seeding** after migrations 0001 + 0002 land. The
     starter-anchor seed migration leaves the 15 question rows with
     NULL embeddings. ``backfill --record-type question`` enqueues
     them.
  2. **Re-embedding on model change.** Operator updates
     ``EMBEDDING_MODEL`` / ``EMBEDDING_MODEL_VERSION`` in the
     environment, then runs ``backfill`` to schedule re-embeds. The
     worker's version-guarded UPDATE handles the rest.

Importantly the worker itself never reads the DB looking for work.
Backfill is the only producer in this repo. Other producers (the
Extraction Worker, Thread Detector, etc.) live in later steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from flashback.db.embedding_targets import EMBEDDING_TARGETS, EmbeddingTarget

from .sqs_client import SQSClient

log = logging.getLogger(__name__)


class _PoolLike(Protocol):
    def connection(self): ...


@dataclass
class BackfillResult:
    record_type: str
    found: int
    enqueued: int


def _scan_query(target: EmbeddingTarget) -> str:
    """
    Pull active rows that still have a NULL vector. We rely on
    `status='active'` for moments/entities/threads/traits/questions
    (none of those tables omit the column).
    """
    return f"""
        SELECT id, ({target.source_sql_expr}) AS source_text
          FROM {target.table}
         WHERE {target.vector_column} IS NULL
           AND status = 'active'
    """


def _scan(pool: _PoolLike, target: EmbeddingTarget) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_scan_query(target))
            for record_id, source_text in cur.fetchall():
                rows.append((str(record_id), source_text))
    return rows


def backfill(
    *,
    pool: _PoolLike,
    sqs: SQSClient,
    embedding_model: str,
    embedding_model_version: str,
    record_types: list[str] | None = None,
    dry_run: bool = False,
) -> list[BackfillResult]:
    """
    Scan and enqueue. Returns a per-record-type summary.

    ``record_types=None`` means "all". Pass a single value (e.g.
    ``["question"]``) to scope the run.

    A NULL ``source_text`` is logged and skipped (cannot embed empty
    text). For ``trait``, the source expression already coalesces
    NULL descriptions; the only way ``source_text`` ends up NULL is a
    NULL trait name, which the schema disallows.
    """
    selected = record_types or list(EMBEDDING_TARGETS.keys())
    results: list[BackfillResult] = []

    for record_type in selected:
        target = EMBEDDING_TARGETS[record_type]
        rows = _scan(pool, target)
        enqueued = 0

        for record_id, source_text in rows:
            if source_text is None or source_text == "":
                log.warning(
                    "backfill.skipped_empty_source",
                    extra={"record_type": record_type, "record_id": record_id},
                )
                continue
            if dry_run:
                continue
            sqs.send_embedding_job(
                record_type=record_type,
                record_id=record_id,
                source_text=source_text,
                embedding_model=embedding_model,
                embedding_model_version=embedding_model_version,
            )
            enqueued += 1

        results.append(BackfillResult(
            record_type=record_type, found=len(rows), enqueued=enqueued,
        ))
        log.info(
            "backfill.scanned",
            extra={
                "record_type": record_type,
                "found": len(rows),
                "enqueued": enqueued,
                "dry_run": dry_run,
            },
        )

    return results
