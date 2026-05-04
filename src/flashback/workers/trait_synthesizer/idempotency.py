"""Idempotency for the Trait Synthesizer.

SQS guarantees at-least-once delivery. The CLI ``run-once`` path is
intended for ad-hoc testing and is best-effort. Both paths share the
``processed_trait_syntheses`` table:

* SQS path:  ``idempotency_key`` is the SQS MessageId.
* CLI path:  ``idempotency_key`` is ``runonce-{person_id}-{ms}``.

The first transaction that successfully synthesizes for a key writes
a row; a redelivery sees the row and ack-and-skips.
"""

from __future__ import annotations

import time
from typing import Any


def is_processed(conn_or_cursor, idempotency_key: str) -> bool:
    """Return True iff this idempotency_key already has a row."""
    cur = _as_cursor(conn_or_cursor)
    cur.execute(
        "SELECT 1 FROM processed_trait_syntheses WHERE idempotency_key = %s",
        (idempotency_key,),
    )
    return cur.fetchone() is not None


def mark_processed(
    cursor,
    *,
    idempotency_key: str,
    person_id: str,
    traits_created: int,
    traits_upgraded: int,
    traits_downgraded: int,
) -> None:
    """Insert the idempotency row inside the synthesis transaction."""
    cursor.execute(
        """
        INSERT INTO processed_trait_syntheses
              (idempotency_key, person_id,
               traits_created, traits_upgraded, traits_downgraded)
        VALUES (%s,             %s,
                %s,             %s,              %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        (
            idempotency_key,
            person_id,
            traits_created,
            traits_upgraded,
            traits_downgraded,
        ),
    )


def make_runonce_key(person_id: str) -> str:
    """Synthetic idempotency key used by the CLI run-once path.

    Suffix is millisecond-precision wall-clock; same person twice in
    rapid succession therefore gets two different keys (two rows).
    Best-effort by design: the CLI is for ops/testing, not steady-state.
    """
    return f"runonce-{person_id}-{int(time.time() * 1000)}"


def _as_cursor(conn_or_cursor: Any):
    """Accept either a psycopg connection or a cursor."""
    if hasattr(conn_or_cursor, "execute"):
        return conn_or_cursor
    return conn_or_cursor.cursor()
