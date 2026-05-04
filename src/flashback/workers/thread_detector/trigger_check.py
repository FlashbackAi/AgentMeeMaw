"""Re-validate the thread-detector trigger on message receive.

The Extraction Worker may push duplicate messages (multiple extractions
fire the trigger before this worker drains it) and SQS may redeliver
late. The worker re-runs the trigger condition in code before doing any
real work; if the condition is no longer satisfied, the message is
acked-and-skipped without writes.

Trigger condition (per CLAUDE.md §4 invariant #14):

    active_count >= 15 AND active_count - moments_at_last_thread_run >= 15
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

log = structlog.get_logger("flashback.workers.thread_detector.trigger_check")


@dataclass(frozen=True)
class TriggerState:
    active_count: int
    last_count: int
    delta: int
    valid: bool


def trigger_state(db_pool, *, person_id: str) -> TriggerState:
    """Read current counts and report whether the trigger is still valid."""
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
        return TriggerState(0, 0, 0, False)

    active_count, last_count = int(row[0]), int(row[1])
    delta = active_count - last_count
    valid = active_count >= 15 and delta >= 15
    return TriggerState(
        active_count=active_count,
        last_count=last_count,
        delta=delta,
        valid=valid,
    )


def update_moments_at_last_thread_run(db_pool, *, person_id: str) -> int:
    """Stamp ``persons.moments_at_last_thread_run`` to the current count.

    Runs in its own transaction. Called after at least one cluster has
    been processed successfully — see CLAUDE.md §4 invariant #14.

    Returns the new value written.
    """
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE persons p
                   SET moments_at_last_thread_run = sub.active_count
                  FROM (
                       SELECT count(*) AS active_count
                         FROM active_moments
                        WHERE person_id = %(pid)s
                       ) sub
                 WHERE p.id = %(pid)s
                RETURNING p.moments_at_last_thread_run
                """,
                {"pid": person_id},
            )
            row = cur.fetchone()
            conn.commit()
    if row is None:
        log.warning(
            "thread_detector.update_last_run_no_person", person_id=person_id
        )
        return 0
    new_value = int(row[0])
    log.info(
        "thread_detector.last_run_updated",
        person_id=person_id,
        new_value=new_value,
    )
    return new_value
