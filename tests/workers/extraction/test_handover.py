"""Handover Check tests."""

from __future__ import annotations

from flashback.workers.extraction.handover import run_handover_check


def _set_coverage(db_pool, person_id: str, **dims: int) -> None:
    state = {
        "sensory": 0,
        "voice": 0,
        "place": 0,
        "relation": 0,
        "era": 0,
        **dims,
    }
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE persons SET coverage_state=%s::jsonb WHERE id=%s",
                (
                    "{"
                    + ",".join(f'"{k}":{v}' for k, v in state.items())
                    + "}",
                    person_id,
                ),
            )
            conn.commit()


def _phase(db_pool, person_id: str) -> tuple[str, object]:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT phase, phase_locked_at FROM persons WHERE id=%s",
                (person_id,),
            )
            return cur.fetchone()


def test_all_zero_coverage_no_flip(db_pool, make_person):
    person_id = make_person("Hand A")
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                flipped = run_handover_check(cur, person_id=person_id)
    assert flipped is False
    phase, locked = _phase(db_pool, person_id)
    assert phase == "starter"
    assert locked is None


def test_partial_coverage_no_flip(db_pool, make_person):
    person_id = make_person("Hand B")
    _set_coverage(
        db_pool, person_id, sensory=1, voice=1, place=1, relation=1, era=0
    )
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                flipped = run_handover_check(cur, person_id=person_id)
    assert flipped is False
    assert _phase(db_pool, person_id)[0] == "starter"


def test_full_coverage_flips_to_steady(db_pool, make_person):
    person_id = make_person("Hand C")
    _set_coverage(
        db_pool, person_id, sensory=1, voice=2, place=1, relation=1, era=1
    )
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                flipped = run_handover_check(cur, person_id=person_id)
    assert flipped is True
    phase, locked = _phase(db_pool, person_id)
    assert phase == "steady"
    assert locked is not None


def test_already_steady_is_noop(db_pool, make_person):
    person_id = make_person("Hand D")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE persons SET phase='steady', phase_locked_at=now() "
                "WHERE id=%s",
                (person_id,),
            )
            conn.commit()
    _set_coverage(
        db_pool, person_id, sensory=1, voice=1, place=1, relation=1, era=1
    )
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                flipped = run_handover_check(cur, person_id=person_id)
    assert flipped is False  # nothing to flip
    assert _phase(db_pool, person_id)[0] == "steady"
