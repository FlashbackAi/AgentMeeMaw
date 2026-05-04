"""Tests for the per-person persistence transaction (DB-touching)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from flashback.workers.profile_summary.persistence import persist_summary


def _fetch_profile_summary(db_pool, person_id: str):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT profile_summary, updated_at FROM persons WHERE id = %s",
                (person_id,),
            )
            return cur.fetchone()


def _fetch_idem_row(db_pool, key: str):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT person_id::text, summary_chars
                     FROM processed_profile_summaries WHERE idempotency_key=%s""",
                (key,),
            )
            return cur.fetchone()


def _run(db_pool, *, person_id: str, summary_text: str, key: str | None = None):
    key = key or f"k-{uuid4()}"
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                result = persist_summary(
                    cur,
                    person_id=person_id,
                    summary_text=summary_text,
                    idempotency_key=key,
                )
    return result, key


def test_persist_summary_updates_profile_summary(db_pool, make_person):
    person_id = make_person("Persist")
    text = "A short summary of who Persist was."
    result, key = _run(db_pool, person_id=person_id, summary_text=text)

    assert result.summary_chars == len(text)

    row = _fetch_profile_summary(db_pool, person_id)
    assert row is not None
    assert row[0] == text
    # updated_at advanced from the seeded row's default.
    assert row[1] is not None

    idem = _fetch_idem_row(db_pool, key)
    assert idem == (person_id, len(text))


def test_persist_summary_bumps_updated_at(db_pool, make_person):
    person_id = make_person("Bump")
    # Capture initial updated_at.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT updated_at FROM persons WHERE id = %s", (person_id,)
            )
            (initial,) = cur.fetchone()

    _run(db_pool, person_id=person_id, summary_text="x")
    after = _fetch_profile_summary(db_pool, person_id)
    assert after[1] >= initial


def test_persist_summary_idempotency_row_correct_chars(db_pool, make_person):
    person_id = make_person("Chars")
    text = "Q" * 432
    _, key = _run(db_pool, person_id=person_id, summary_text=text)
    row = _fetch_idem_row(db_pool, key)
    assert row[1] == 432


def test_persist_summary_transaction_atomicity_on_failure(db_pool, make_person):
    """If something raises mid-transaction, neither the UPDATE nor the
    idempotency INSERT survives."""
    person_id = make_person("Atomic")
    key = f"atom-{uuid4()}"
    # Sanity: starting profile_summary is NULL.
    assert _fetch_profile_summary(db_pool, person_id)[0] is None

    with pytest.raises(Exception):
        with db_pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    persist_summary(
                        cur,
                        person_id=person_id,
                        summary_text="should be rolled back",
                        idempotency_key=key,
                    )
                    # Force a failure inside the transaction.
                    cur.execute("SELECT 1/0")

    # Profile summary not updated.
    assert _fetch_profile_summary(db_pool, person_id)[0] is None
    # Idempotency row not written.
    assert _fetch_idem_row(db_pool, key) is None


def test_persist_summary_overwrites_existing_summary(db_pool, make_person):
    person_id = make_person("Over")
    _, key1 = _run(db_pool, person_id=person_id, summary_text="first")
    _, key2 = _run(db_pool, person_id=person_id, summary_text="second")
    assert _fetch_profile_summary(db_pool, person_id)[0] == "second"
    # Both idempotency rows persist independently.
    assert _fetch_idem_row(db_pool, key1) is not None
    assert _fetch_idem_row(db_pool, key2) is not None
