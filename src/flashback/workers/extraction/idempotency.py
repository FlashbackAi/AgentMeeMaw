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

import json
from typing import Any

NOTIFY_CHANNEL = "extraction_complete"


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
    entities_written: int = 0,
    traits_written: int = 0,
    is_final: bool = False,
    status: str = "done",
) -> bool:
    """Record the segment as processed AND announce completion.

    Inserts the idempotency/status row and, only when that insert actually
    happens (i.e. not an ON CONFLICT no-op redelivery), issues a
    transactional ``pg_notify`` on ``extraction_complete``. Because the
    NOTIFY runs in the caller's transaction, it is delivered iff that
    transaction commits and never on rollback — so a failed extraction
    announces nothing, and a zero-moment success still announces (with
    ``moments_written=0``). Returns True iff a row was inserted.

    Postgres is authoritative (the ``session_extraction_status`` view);
    this notification is the low-latency wake-up only (CLAUDE.md §3).
    """
    cursor.execute(
        """
        INSERT INTO processed_extractions
              (sqs_message_id, person_id, session_id, moments_written,
               entities_written, traits_written, is_final, status)
        VALUES (%s,            %s,        %s,         %s,
               %s,              %s,             %s,       %s)
        ON CONFLICT (sqs_message_id) DO NOTHING
        RETURNING sqs_message_id
        """,
        (
            message_id, person_id, session_id, moments_written,
            entities_written, traits_written, is_final, status,
        ),
    )
    inserted = cursor.fetchone() is not None
    if inserted:
        payload = json.dumps(
            {
                "event": "extraction_complete",
                "session_id": session_id,
                "person_id": person_id,
                "segment_message_id": message_id,
                "is_final": is_final,
                "status": status,
                "moments_written": moments_written,
            }
        )
        cursor.execute("SELECT pg_notify(%s, %s)", (NOTIFY_CHANNEL, payload))
    return inserted


def _as_cursor(conn_or_cursor: Any):
    """Accept either a psycopg connection or a cursor."""
    if hasattr(conn_or_cursor, "execute"):
        return conn_or_cursor
    return conn_or_cursor.cursor()
