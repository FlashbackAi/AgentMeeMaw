"""Thread Detector trigger logic — pushes to SQS in step 12."""

from __future__ import annotations

from flashback.workers.extraction.thread_trigger import (
    check_and_push_thread_detector_trigger,
    check_thread_detector_trigger,
)
from tests.workers.extraction.conftest import StubSQSThreadDetectorSender


def _bulk_insert_moments(db_pool, person_id: str, n: int) -> None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            for i in range(n):
                cur.execute(
                    """
                    INSERT INTO moments (person_id, title, narrative)
                    VALUES (%s, %s, %s)
                    """,
                    (person_id, f"t{i}", f"narr {i}"),
                )
            conn.commit()


def _set_last_count(db_pool, person_id: str, value: int) -> None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE persons SET moments_at_last_thread_run=%s WHERE id=%s",
                (value, person_id),
            )
            conn.commit()


def test_below_15_moments_no_push(db_pool, make_person):
    person_id = make_person("Tri A")
    _bulk_insert_moments(db_pool, person_id, 10)
    sender = StubSQSThreadDetectorSender()

    status = check_and_push_thread_detector_trigger(
        db_pool, person_id=person_id, sender=sender
    )

    assert status.active_count == 10
    assert status.would_trigger is False
    assert status.pushed is False
    assert sender.sent == []


def test_at_15_with_zero_baseline_pushes(db_pool, make_person):
    person_id = make_person("Tri B")
    _bulk_insert_moments(db_pool, person_id, 15)
    sender = StubSQSThreadDetectorSender()

    status = check_and_push_thread_detector_trigger(
        db_pool, person_id=person_id, sender=sender
    )

    assert status.active_count == 15
    assert status.delta == 15
    assert status.would_trigger is True
    assert status.pushed is True
    assert status.sqs_message_id is not None
    assert len(sender.sent) == 1
    body = sender.sent[0]
    assert body["person_id"] == person_id
    assert body["active_count_at_trigger"] == 15
    assert body["last_count_at_trigger"] == 0


def test_30_with_15_baseline_pushes(db_pool, make_person):
    person_id = make_person("Tri C")
    _bulk_insert_moments(db_pool, person_id, 30)
    _set_last_count(db_pool, person_id, 15)
    sender = StubSQSThreadDetectorSender()

    status = check_and_push_thread_detector_trigger(
        db_pool, person_id=person_id, sender=sender
    )

    assert status.active_count == 30
    assert status.delta == 15
    assert status.pushed is True
    assert len(sender.sent) == 1


def test_30_with_20_baseline_does_not_push(db_pool, make_person):
    person_id = make_person("Tri D")
    _bulk_insert_moments(db_pool, person_id, 30)
    _set_last_count(db_pool, person_id, 20)
    sender = StubSQSThreadDetectorSender()

    status = check_and_push_thread_detector_trigger(
        db_pool, person_id=person_id, sender=sender
    )

    assert status.active_count == 30
    assert status.delta == 10
    assert status.would_trigger is False
    assert status.pushed is False
    assert sender.sent == []


def test_check_only_does_not_push(db_pool, make_person):
    """``check_thread_detector_trigger`` is read-only; never sends SQS."""
    person_id = make_person("Tri E")
    _bulk_insert_moments(db_pool, person_id, 15)

    status = check_thread_detector_trigger(db_pool, person_id=person_id)

    assert status.would_trigger is True
    # The legacy entry-point doesn't carry a sender, so pushed stays False.
    assert status.pushed is False
