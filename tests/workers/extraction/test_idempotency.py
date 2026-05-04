"""Idempotency table tests (DB-touching)."""

from __future__ import annotations

from uuid import uuid4

from flashback.workers.extraction.idempotency import is_processed, mark_processed


def test_first_time_returns_false_then_writes(db_pool, make_person):
    person_id = make_person("Idem A")
    message_id = f"msg-{uuid4()}"
    session_id = str(uuid4())

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            assert is_processed(cur, message_id) is False
            mark_processed(
                cur,
                message_id=message_id,
                person_id=person_id,
                session_id=session_id,
                moments_written=2,
            )
            conn.commit()

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            assert is_processed(cur, message_id) is True


def test_second_time_is_noop(db_pool, make_person):
    person_id = make_person("Idem B")
    message_id = f"msg-{uuid4()}"
    session_id = str(uuid4())

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            mark_processed(
                cur,
                message_id=message_id,
                person_id=person_id,
                session_id=session_id,
                moments_written=1,
            )
            mark_processed(
                cur,
                message_id=message_id,
                person_id=person_id,
                session_id=session_id,
                moments_written=99,  # would-be overwrite
            )
            conn.commit()

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT moments_written FROM processed_extractions WHERE sqs_message_id=%s",
                (message_id,),
            )
            (count,) = cur.fetchone()
    assert count == 1  # ON CONFLICT DO NOTHING preserved the original
