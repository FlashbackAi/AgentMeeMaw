"""
SQS-message idempotency for the Extraction Worker.

SQS guarantees at-least-once delivery: the worker will occasionally see
the same message twice (visibility-timeout expiry, redrive, or upstream
retry from the orchestrator's "SQS push fails, segment re-evaluated next
turn" edge case).

We key on the SQS MessageId. The first transaction that successfully
extracts a segment writes a row into ``processed_extractions``; a
redelivery sees the row and ack-and-skips.

The write happens INSIDE the same transaction that persists the
extraction, so processed-status and graph state move together.
"""

from __future__ import annotations

from typing import Any


def is_processed(conn_or_cursor, message_id: str) -> bool:
    """Return True iff this MessageId already has a processed_extractions row."""
    cur = _as_cursor(conn_or_cursor)
    cur.execute(
        "SELECT 1 FROM processed_extractions WHERE sqs_message_id = %s",
        (message_id,),
    )
    return cur.fetchone() is not None


def mark_processed(
    cursor,
    *,
    message_id: str,
    person_id: str,
    session_id: str,
    moments_written: int,
) -> None:
    """Insert the idempotency row inside the extraction transaction."""
    cursor.execute(
        """
        INSERT INTO processed_extractions
              (sqs_message_id, person_id, session_id, moments_written)
        VALUES (%s,            %s,        %s,         %s)
        ON CONFLICT (sqs_message_id) DO NOTHING
        """,
        (message_id, person_id, session_id, moments_written),
    )


def _as_cursor(conn_or_cursor: Any):
    """Accept either a psycopg connection or a cursor."""
    if hasattr(conn_or_cursor, "execute"):
        return conn_or_cursor
    return conn_or_cursor.cursor()
