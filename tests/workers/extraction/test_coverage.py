"""Coverage Tracker tests."""

from __future__ import annotations

from flashback.workers.extraction.coverage import run_coverage_tracker
from flashback.workers.extraction.persistence import MomentCoverageSignal


def _coverage_state(db_pool, person_id: str) -> dict[str, int]:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT coverage_state FROM persons WHERE id=%s", (person_id,)
            )
            (state,) = cur.fetchone()
    return {k: int(v) for k, v in state.items()}


def test_each_dimension_increments(db_pool, make_person):
    person_id = make_person("Cov A")
    signals = [
        MomentCoverageSignal(
            has_sensory=True,
            has_voice=True,
            has_place=True,
            has_non_subject_person=True,
            has_era=True,
        )
    ]
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                run_coverage_tracker(
                    cur, person_id=person_id, moment_signals=signals
                )
    assert _coverage_state(db_pool, person_id) == {
        "sensory": 1,
        "voice": 1,
        "place": 1,
        "relation": 1,
        "era": 1,
    }


def test_zero_signals_is_noop(db_pool, make_person):
    person_id = make_person("Cov B")
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                run_coverage_tracker(cur, person_id=person_id, moment_signals=[])
    assert _coverage_state(db_pool, person_id) == {
        "sensory": 0,
        "voice": 0,
        "place": 0,
        "relation": 0,
        "era": 0,
    }


def test_multiple_moments_accumulate(db_pool, make_person):
    person_id = make_person("Cov C")
    signals = [
        MomentCoverageSignal(
            has_sensory=True,
            has_voice=False,
            has_place=True,
            has_non_subject_person=True,
            has_era=False,
        ),
        MomentCoverageSignal(
            has_sensory=True,
            has_voice=True,
            has_place=False,
            has_non_subject_person=False,
            has_era=True,
        ),
    ]
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                run_coverage_tracker(
                    cur, person_id=person_id, moment_signals=signals
                )
    assert _coverage_state(db_pool, person_id) == {
        "sensory": 2,
        "voice": 1,
        "place": 1,
        "relation": 1,
        "era": 1,
    }


def test_repeated_calls_keep_climbing(db_pool, make_person):
    """Counters can climb past 1; only ≥1 matters for handover."""
    person_id = make_person("Cov D")
    signals = [
        MomentCoverageSignal(
            has_sensory=True,
            has_voice=True,
            has_place=True,
            has_non_subject_person=True,
            has_era=True,
        )
    ]
    for _ in range(3):
        with db_pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    run_coverage_tracker(
                        cur, person_id=person_id, moment_signals=signals
                    )
    assert _coverage_state(db_pool, person_id) == {
        "sensory": 3,
        "voice": 3,
        "place": 3,
        "relation": 3,
        "era": 3,
    }
