"""Re-validation of the 15-moment gate (DB-touching)."""

from __future__ import annotations

from flashback.workers.thread_detector.trigger_check import (
    trigger_state,
    update_moments_at_last_thread_run,
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


def test_below_15_is_invalid(db_pool, make_person):
    person_id = make_person("Trig A")
    _bulk_insert_moments(db_pool, person_id, 14)

    state = trigger_state(db_pool, person_id=person_id)

    assert state.active_count == 14
    assert state.valid is False


def test_at_15_with_zero_baseline_is_valid(db_pool, make_person):
    person_id = make_person("Trig B")
    _bulk_insert_moments(db_pool, person_id, 15)

    state = trigger_state(db_pool, person_id=person_id)

    assert state.active_count == 15
    assert state.last_count == 0
    assert state.delta == 15
    assert state.valid is True


def test_30_with_15_baseline_is_valid(db_pool, make_person):
    person_id = make_person("Trig C")
    _bulk_insert_moments(db_pool, person_id, 30)
    _set_last_count(db_pool, person_id, 15)

    state = trigger_state(db_pool, person_id=person_id)

    assert state.delta == 15
    assert state.valid is True


def test_30_with_20_baseline_is_invalid(db_pool, make_person):
    person_id = make_person("Trig D")
    _bulk_insert_moments(db_pool, person_id, 30)
    _set_last_count(db_pool, person_id, 20)

    state = trigger_state(db_pool, person_id=person_id)

    assert state.delta == 10
    assert state.valid is False


def test_16_with_zero_baseline_is_valid(db_pool, make_person):
    person_id = make_person("Trig E")
    _bulk_insert_moments(db_pool, person_id, 16)

    state = trigger_state(db_pool, person_id=person_id)

    assert state.delta == 16
    assert state.valid is True


def test_update_moments_at_last_thread_run_sets_to_active_count(
    db_pool, make_person
):
    person_id = make_person("Trig F")
    _bulk_insert_moments(db_pool, person_id, 17)

    written = update_moments_at_last_thread_run(db_pool, person_id=person_id)

    assert written == 17

    # Re-validate: trigger now invalid (delta = 0).
    state = trigger_state(db_pool, person_id=person_id)
    assert state.last_count == 17
    assert state.valid is False
