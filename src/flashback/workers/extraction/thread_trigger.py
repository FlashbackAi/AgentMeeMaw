"""
Thread Detector trigger (per ARCHITECTURE.md §3.13).

After a successful extraction commit, the Extraction Worker calls
:func:`check_and_push_thread_detector_trigger`. If the count-based
trigger condition is satisfied — total active moments ≥ 15 AND
``active_count - moments_at_last_thread_run`` ≥ 15 — the function
pushes one message onto the ``thread_detector`` SQS queue. The Thread
Detector worker (step 12) drains that queue.

The trigger ONLY pushes; it does not update
``persons.moments_at_last_thread_run``. That column is updated by the
Thread Detector worker at the end of a successful run, so multiple
trigger pushes between the first push and the eventual run all observe
the same baseline. The worker re-validates the trigger on receive to
discard stale messages.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from flashback.workers.thread_detector.sqs_client import ThreadDetectorJobSender

log = structlog.get_logger("flashback.workers.extraction.thread_trigger")


@dataclass(frozen=True)
class ThreadTriggerStatus:
    active_count: int
    last_count: int
    delta: int
    would_trigger: bool
    pushed: bool = False
    sqs_message_id: str | None = None


def check_thread_detector_trigger(
    db_pool, *, person_id: str
) -> ThreadTriggerStatus:
    """Read post-commit moment counts and report whether the trigger fires.

    Pure read + log. Callers that want the message pushed should use
    :func:`check_and_push_thread_detector_trigger` instead. Kept as the
    legacy entry-point so analysis paths can observe the same state
    without producing an SQS side-effect.
    """
    return _check(db_pool, person_id=person_id)


def check_and_push_thread_detector_trigger(
    db_pool,
    *,
    person_id: str,
    sender: ThreadDetectorJobSender,
) -> ThreadTriggerStatus:
    """Read counts; if the trigger fires, push to the thread_detector queue.

    Returns a :class:`ThreadTriggerStatus` whose ``pushed`` field reports
    whether an SQS send happened. The caller owns ack-ing the *extraction*
    message; this function does not touch SQS visibility on that side.
    """
    status = _check(db_pool, person_id=person_id)
    if not status.would_trigger:
        return status

    message_id = sender.send(
        person_id=person_id,
        active_count_at_trigger=status.active_count,
        last_count_at_trigger=status.last_count,
    )
    log.info(
        "thread_detector_triggered",
        person_id=person_id,
        active_count=status.active_count,
        last_count=status.last_count,
        delta=status.delta,
        sqs_message_id=message_id,
    )
    return ThreadTriggerStatus(
        active_count=status.active_count,
        last_count=status.last_count,
        delta=status.delta,
        would_trigger=True,
        pushed=True,
        sqs_message_id=message_id,
    )


def _check(db_pool, *, person_id: str) -> ThreadTriggerStatus:
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
