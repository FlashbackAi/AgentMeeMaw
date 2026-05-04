"""Idempotency for the Profile Summary Generator.

SQS guarantees at-least-once delivery. The CLI ``run-once`` path is
intended for ad-hoc testing and is best-effort. Both paths share the
``processed_profile_summaries`` table:

* SQS path:  ``idempotency_key`` is the SQS MessageId.
* CLI path:  ``idempotency_key`` is ``runonce-{person_id}-{ms}``.

Two callers are interesting:

* The happy path writes the row inside the persistence transaction
  (alongside the UPDATE on ``persons.profile_summary``).
* The empty-legacy short-circuit writes only an idempotency row with
  ``summary_chars=0`` so a redelivery doesn't repeat the no-op.

A second run for the same key is skipped (worker-level idempotency).
A NEW key for the same person produces a fresh summary that overwrites
the previous one — that is the desired behavior, profile summaries get
fresher as more is recorded.
"""

from __future__ import annotations

import time


def is_processed(cursor, idempotency_key: str) -> bool:
    """Return True iff this idempotency_key already has a row.

    Pass a psycopg cursor (the runner does this from inside its own
    ``with conn.cursor() as cur`` block). Connections aren't accepted
    here — psycopg-3 connections have ``execute`` as a shortcut that
    returns a fresh cursor, which would silently break ``fetchone``.
    """
    cursor.execute(
        "SELECT 1 FROM processed_profile_summaries WHERE idempotency_key = %s",
        (idempotency_key,),
    )
    return cursor.fetchone() is not None


def mark_processed(
    cursor,
    *,
    idempotency_key: str,
    person_id: str,
    summary_chars: int,
) -> None:
    """Insert the idempotency row.

    Caller owns transaction control. The persistence layer calls this
    inside the same transaction as the ``persons`` UPDATE; the empty-
    legacy path opens its own short transaction.
    """
    cursor.execute(
        """
        INSERT INTO processed_profile_summaries
              (idempotency_key, person_id, summary_chars)
        VALUES (%s,             %s,        %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        (idempotency_key, person_id, summary_chars),
    )


def mark_processed_empty(
    db_pool,
    *,
    idempotency_key: str,
    person_id: str,
) -> None:
    """Convenience: write the empty-legacy idempotency row in its own tx.

    The empty-legacy short-circuit doesn't touch ``persons``, so there
    is no other write to bundle with. This helper opens a connection,
    inserts the row, and commits.
    """
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                mark_processed(
                    cur,
                    idempotency_key=idempotency_key,
                    person_id=person_id,
                    summary_chars=0,
                )


def make_runonce_key(person_id: str) -> str:
    """Synthetic idempotency key used by the CLI run-once path.

    Suffix is millisecond-precision wall-clock; same person twice in
    rapid succession therefore gets two different keys (two rows).
    Best-effort by design: the CLI is for ops/testing, not steady-state.
    """
    return f"runonce-{person_id}-{int(time.time() * 1000)}"
