"""Tests for the idempotency helpers."""

from __future__ import annotations

import re
import time

from flashback.workers.profile_summary.idempotency import (
    is_processed,
    make_runonce_key,
    mark_processed,
    mark_processed_empty,
)


def test_make_runonce_key_shape() -> None:
    pid = "abc-123"
    key = make_runonce_key(pid)
    assert key.startswith(f"runonce-{pid}-")
    suffix = key[len(f"runonce-{pid}-"):]
    assert re.fullmatch(r"\d+", suffix) is not None
    assert int(suffix) <= int(time.time() * 1000) + 100


def test_make_runonce_key_uniqueness() -> None:
    pid = "abc-123"
    a = make_runonce_key(pid)
    time.sleep(0.002)
    b = make_runonce_key(pid)
    assert a != b


def test_is_processed_false_then_true_after_mark(db_pool, make_person):
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
                    summary_chars=42,
                )
        with conn.cursor() as cur:
            assert is_processed(cur, key) is True


def test_mark_processed_on_conflict_do_nothing(db_pool, make_person):
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
                    summary_chars=10,
                )
        with conn.transaction():
            with conn.cursor() as cur:
                mark_processed(
                    cur,
                    idempotency_key=key,
                    person_id=person_id,
                    summary_chars=999,  # would-be-overwrite — must not stick
                )
        with conn.cursor() as cur:
            cur.execute(
                """SELECT summary_chars FROM processed_profile_summaries
                    WHERE idempotency_key=%s""",
                (key,),
            )
            (chars,) = cur.fetchone()
    assert chars == 10


def test_mark_processed_empty_writes_zero_chars(db_pool, make_person):
    person_id = make_person("EmptyMark")
    key = f"empty-{person_id}"
    mark_processed_empty(db_pool, idempotency_key=key, person_id=person_id)
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT person_id::text, summary_chars
                     FROM processed_profile_summaries
                    WHERE idempotency_key=%s""",
                (key,),
            )
            row = cur.fetchone()
    assert row == (person_id, 0)


def test_is_processed_via_cursor(db_pool, make_person):
    person_id = make_person("Either")
    key = "k-either"
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                mark_processed(
                    cur,
                    idempotency_key=key,
                    person_id=person_id,
                    summary_chars=5,
                )
        with conn.cursor() as cur:
            assert is_processed(cur, key) is True
