"""Tests for the idempotency helpers."""

from __future__ import annotations

import re
import time

from flashback.workers.trait_synthesizer.idempotency import (
    is_processed,
    make_runonce_key,
    mark_processed,
)


def test_make_runonce_key_shape() -> None:
    pid = "abc-123"
    key = make_runonce_key(pid)
    assert key.startswith(f"runonce-{pid}-")
    suffix = key[len(f"runonce-{pid}-"):]
    assert re.fullmatch(r"\d+", suffix) is not None
    # Roughly within current ms-time
    assert int(suffix) <= int(time.time() * 1000) + 100


def test_make_runonce_key_uniqueness() -> None:
    pid = "abc-123"
    a = make_runonce_key(pid)
    time.sleep(0.002)
    b = make_runonce_key(pid)
    assert a != b


def test_is_processed_false_then_true_after_mark(db_pool, make_person) -> None:
    person_id = make_person("Idem")
    key = "test-key-1"
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            assert is_processed(cur, key) is False
        with conn.transaction():
            with conn.cursor() as cur:
                mark_processed(
                    cur,
                    idempotency_key=key,
                    person_id=person_id,
                    traits_created=2,
                    traits_upgraded=1,
                    traits_downgraded=0,
                )
        with conn.cursor() as cur:
            assert is_processed(cur, key) is True


def test_mark_processed_on_conflict_do_nothing(db_pool, make_person) -> None:
    """Re-marking the same key must not raise (ON CONFLICT DO NOTHING)."""
    person_id = make_person("Conflict")
    key = "test-key-conflict"
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                mark_processed(
                    cur,
                    idempotency_key=key,
                    person_id=person_id,
                    traits_created=1,
                    traits_upgraded=0,
                    traits_downgraded=0,
                )
        with conn.transaction():
            with conn.cursor() as cur:
                mark_processed(
                    cur,
                    idempotency_key=key,
                    person_id=person_id,
                    traits_created=99,  # would-be-overwrite — must not stick
                    traits_upgraded=99,
                    traits_downgraded=99,
                )
        with conn.cursor() as cur:
            cur.execute(
                """SELECT traits_created, traits_upgraded, traits_downgraded
                     FROM processed_trait_syntheses WHERE idempotency_key=%s""",
                (key,),
            )
            row = cur.fetchone()
    # Original counts preserved.
    assert row == (1, 0, 0)


def test_is_processed_accepts_connection_or_cursor(db_pool, make_person) -> None:
    person_id = make_person("Either")
    key = "k-either"
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                mark_processed(
                    cur,
                    idempotency_key=key,
                    person_id=person_id,
                    traits_created=0,
                    traits_upgraded=0,
                    traits_downgraded=0,
                )
        with conn.cursor() as cur:
            assert is_processed(cur, key) is True
