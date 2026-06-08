"""Idempotency table tests (DB-touching)."""

from __future__ import annotations

import json
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


def test_mark_processed_writes_signal_columns(db_pool, make_person):
    person_id = make_person("Idem Cols")
    message_id = f"msg-{uuid4()}"
    session_id = str(uuid4())

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            mark_processed(
                cur,
                message_id=message_id,
                person_id=person_id,
                session_id=session_id,
                moments_written=3,
                entities_written=2,
                traits_written=1,
                is_final=True,
            )
        conn.commit()

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT moments_written, entities_written, traits_written,
                       is_final, status
                  FROM processed_extractions WHERE sqs_message_id=%s
                """,
                (message_id,),
            )
            row = cur.fetchone()
    assert row == (3, 2, 1, True, "done")


def test_mark_processed_emits_notification(db_pool, make_person):
    person_id = make_person("Notify One")
    message_id = f"msg-{uuid4()}"
    session_id = str(uuid4())

    with db_pool.connection() as listen_conn:
        listen_conn.autocommit = True
        listen_conn.execute("LISTEN extraction_complete")
        try:
            with db_pool.connection() as conn:
                with conn.cursor() as cur:
                    mark_processed(
                        cur,
                        message_id=message_id,
                        person_id=person_id,
                        session_id=session_id,
                        moments_written=0,   # zero-moment segment STILL notifies
                        is_final=True,
                    )
                conn.commit()

            # psycopg3 generator; stop after the first notification or 5s.
            received = [n for n in listen_conn.notifies(timeout=5, stop_after=1)]
        finally:
            listen_conn.execute("UNLISTEN *")

    assert len(received) == 1
    payload = json.loads(received[0].payload)
    assert payload["event"] == "extraction_complete"
    assert payload["session_id"] == session_id
    assert payload["segment_message_id"] == message_id
    assert payload["is_final"] is True
    assert payload["moments_written"] == 0
    assert payload["status"] == "done"


def test_mark_processed_does_not_double_notify_on_conflict(db_pool, make_person):
    person_id = make_person("Notify Dedup")
    message_id = f"msg-{uuid4()}"
    session_id = str(uuid4())

    with db_pool.connection() as listen_conn:
        listen_conn.autocommit = True
        listen_conn.execute("LISTEN extraction_complete")
        try:
            with db_pool.connection() as conn:
                with conn.cursor() as cur:
                    first = mark_processed(
                        cur, message_id=message_id, person_id=person_id,
                        session_id=session_id, moments_written=1,
                    )
                    second = mark_processed(  # same id -> ON CONFLICT DO NOTHING
                        cur, message_id=message_id, person_id=person_id,
                        session_id=session_id, moments_written=1,
                    )
                conn.commit()

            received = [n for n in listen_conn.notifies(timeout=2, stop_after=2)]
        finally:
            listen_conn.execute("UNLISTEN *")

    assert first is True
    assert second is False
    assert len(received) == 1  # only the inserting call notified
