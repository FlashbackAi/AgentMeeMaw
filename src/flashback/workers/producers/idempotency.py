"""Idempotency helpers for P2/P3/P5 producer runs."""

from __future__ import annotations

import time


def is_processed(cursor, idempotency_key: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM processed_producer_runs WHERE idempotency_key = %s",
        (idempotency_key,),
    )
    return cursor.fetchone() is not None


def mark_processed(
    cursor,
    *,
    idempotency_key: str,
    person_id: str,
    producer: str,
    questions_written: int,
) -> None:
    cursor.execute(
        """
        INSERT INTO processed_producer_runs
              (idempotency_key, person_id, producer, questions_written)
        VALUES (%s,              %s,        %s,       %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        (idempotency_key, person_id, producer, questions_written),
    )


def mark_processed_empty(
    db_pool,
    *,
    idempotency_key: str,
    person_id: str,
    producer: str,
) -> None:
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                mark_processed(
                    cur,
                    idempotency_key=idempotency_key,
                    person_id=person_id,
                    producer=producer,
                    questions_written=0,
                )


def make_runonce_key(producer: str, person_id: str) -> str:
    return f"runonce-{producer}-{person_id}-{int(time.time() * 1000)}"

