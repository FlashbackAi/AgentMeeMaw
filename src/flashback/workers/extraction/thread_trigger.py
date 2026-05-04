"""
Thread Detector trigger logging (per ARCHITECTURE.md §3.13).

The Thread Detector itself lands in step 14 with its own queue and
worker. Step 11 only logs the trigger condition so we can verify the
math before introducing the next moving part.

Trigger rule:
    total active moments ≥ 15 AND
    (active_count - moments_at_last_thread_run) ≥ 15
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

log = structlog.get_logger("flashback.workers.extraction.thread_trigger")


@dataclass(frozen=True)
class ThreadTriggerStatus:
    active_count: int
    last_count: int
    delta: int
    would_trigger: bool


def check_thread_detector_trigger(
    db_pool, *, person_id: str
) -> ThreadTriggerStatus:
    """Read post-commit moment counts and log if the trigger fires."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
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
            row = cur.fetchone()
    if row is None:
        return ThreadTriggerStatus(0, 0, 0, False)

    active_count, last_count = int(row[0]), int(row[1])
    delta = active_count - last_count
    would_trigger = active_count >= 15 and delta >= 15
    if would_trigger:
        log.info(
            "would_trigger_thread_detector",
            person_id=person_id,
            active_count=active_count,
            last_count=last_count,
            delta=delta,
        )
    return ThreadTriggerStatus(
        active_count=active_count,
        last_count=last_count,
        delta=delta,
        would_trigger=would_trigger,
    )
