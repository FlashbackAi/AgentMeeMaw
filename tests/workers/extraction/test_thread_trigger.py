"""Thread Detector trigger logic — log-only in step 11."""

from __future__ import annotations

import logging

from flashback.workers.extraction.thread_trigger import (
    check_thread_detector_trigger,
)


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


def test_below_15_moments_no_trigger(db_pool, make_person):
    person_id = make_person("Tri A")
    _bulk_insert_moments(db_pool, person_id, 10)
    status = check_thread_detector_trigger(db_pool, person_id=person_id)
    assert status.active_count == 10
    assert status.would_trigger is False


def test_at_15_with_zero_baseline_triggers(db_pool, make_person):
    person_id = make_person("Tri B")
    _bulk_insert_moments(db_pool, person_id, 15)
    status = check_thread_detector_trigger(db_pool, person_id=person_id)
    assert status.active_count == 15
    assert status.delta == 15
    assert status.would_trigger is True


def test_30_with_15_baseline_triggers(db_pool, make_person):
    person_id = make_person("Tri C")
    _bulk_insert_moments(db_pool, person_id, 30)
    _set_last_count(db_pool, person_id, 15)
    status = check_thread_detector_trigger(db_pool, person_id=person_id)
    assert status.active_count == 30
    assert status.delta == 15
    assert status.would_trigger is True


def test_30_with_20_baseline_does_not_trigger(db_pool, make_person):
    person_id = make_person("Tri D")
    _bulk_insert_moments(db_pool, person_id, 30)
    _set_last_count(db_pool, person_id, 20)
    status = check_thread_detector_trigger(db_pool, person_id=person_id)
    assert status.active_count == 30
    assert status.delta == 10
    assert status.would_trigger is False
