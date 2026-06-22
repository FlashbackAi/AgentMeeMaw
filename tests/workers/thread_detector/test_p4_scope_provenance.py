"""DB-gated tests for _fetch_thread_moment_ids + _resolve_single_contributor.

Seeds data directly via SQL (no process_cluster) to verify that:
  - _fetch_thread_moment_ids returns all moment ids linked via active
    evidences edges, not just the current cluster's members.
  - _resolve_single_contributor returns NULL when moments span two
    different contributors (cross-contributor thread).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from flashback.workers.thread_detector.persistence import (
    _fetch_member_told_by,
    _fetch_thread_moment_ids,
    _resolve_single_contributor,
)


# ---------------------------------------------------------------------------
# Existing unit tests (no DB)
# ---------------------------------------------------------------------------


def test_single_contributor_members_resolve_to_that_user():
    u = "11111111-1111-1111-1111-111111111111"
    assert _resolve_single_contributor([u, None, u]) == u


def test_mixed_contributors_resolve_to_none():
    a = "11111111-1111-1111-1111-111111111111"
    b = "22222222-2222-2222-2222-222222222222"
    assert _resolve_single_contributor([a, b, None]) is None


def test_all_null_members_resolve_to_none():
    assert _resolve_single_contributor([None, None]) is None
    assert _resolve_single_contributor([]) is None


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_person(db_pool, name: str) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO persons (name) VALUES (%s) RETURNING id::text",
                (name,),
            )
            person_id = cur.fetchone()[0]
            conn.commit()
    return person_id


def _seed_thread(db_pool, person_id: str) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO threads (person_id, name, description)
                VALUES (%s, 'Test thread', 'A test thread for provenance.')
                RETURNING id::text
                """,
                (person_id,),
            )
            thread_id = cur.fetchone()[0]
            conn.commit()
    return thread_id


def _seed_moment(db_pool, person_id: str, told_by_user_id: str | None) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments
                      (person_id, title, narrative, status, told_by_user_id)
                VALUES (%s, 'A moment', 'Some narrative.', 'active', %s)
                RETURNING id::text
                """,
                (person_id, told_by_user_id),
            )
            moment_id = cur.fetchone()[0]
            conn.commit()
    return moment_id


def _seed_evidences_edge(db_pool, moment_id: str, thread_id: str) -> None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO edges
                      (from_kind, from_id, to_kind, to_id, edge_type, attributes)
                VALUES ('moment', %s::uuid, 'thread', %s::uuid, 'evidences', '{}')
                """,
                (moment_id, thread_id),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# DB-gated tests
# ---------------------------------------------------------------------------


def test_fetch_thread_moment_ids_returns_all_linked_moments(db_pool):
    """_fetch_thread_moment_ids returns every moment linked via active
    evidences edges, not just a cluster subset.
    """
    person_id = _seed_person(db_pool, "Cross-contrib subject")
    thread_id = _seed_thread(db_pool, person_id)
    user_a = str(uuid4())
    user_b = str(uuid4())
    mid1 = _seed_moment(db_pool, person_id, user_a)
    mid2 = _seed_moment(db_pool, person_id, user_b)
    _seed_evidences_edge(db_pool, mid1, thread_id)
    _seed_evidences_edge(db_pool, mid2, thread_id)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            result = _fetch_thread_moment_ids(cur, thread_id)

    assert sorted(result) == sorted([mid1, mid2])


def test_cross_contributor_thread_resolves_to_null(db_pool):
    """A thread whose moments span two distinct contributors → told_by=NULL."""
    person_id = _seed_person(db_pool, "Cross-contrib subject 2")
    thread_id = _seed_thread(db_pool, person_id)
    user_a = str(uuid4())
    user_b = str(uuid4())
    mid1 = _seed_moment(db_pool, person_id, user_a)
    mid2 = _seed_moment(db_pool, person_id, user_b)
    _seed_evidences_edge(db_pool, mid1, thread_id)
    _seed_evidences_edge(db_pool, mid2, thread_id)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            moment_ids = _fetch_thread_moment_ids(cur, thread_id)
            told_by_rows = _fetch_member_told_by(cur, moment_ids)

    result = _resolve_single_contributor(told_by_rows)
    assert result is None, (
        f"Expected NULL for cross-contributor thread, got {result!r}"
    )


def test_single_contributor_thread_resolves_to_that_user(db_pool):
    """A thread whose moments all share the same contributor stamps that user."""
    person_id = _seed_person(db_pool, "Single-contrib subject")
    thread_id = _seed_thread(db_pool, person_id)
    user_a = str(uuid4())
    mid1 = _seed_moment(db_pool, person_id, user_a)
    mid2 = _seed_moment(db_pool, person_id, user_a)
    _seed_evidences_edge(db_pool, mid1, thread_id)
    _seed_evidences_edge(db_pool, mid2, thread_id)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            moment_ids = _fetch_thread_moment_ids(cur, thread_id)
            told_by_rows = _fetch_member_told_by(cur, moment_ids)

    result = _resolve_single_contributor(told_by_rows)
    assert result == user_a


def test_all_null_contributors_resolves_to_null(db_pool):
    """Creator-era moments (told_by_user_id=NULL) → NULL (shared/unowned)."""
    person_id = _seed_person(db_pool, "Null-contrib subject")
    thread_id = _seed_thread(db_pool, person_id)
    mid1 = _seed_moment(db_pool, person_id, None)
    mid2 = _seed_moment(db_pool, person_id, None)
    _seed_evidences_edge(db_pool, mid1, thread_id)
    _seed_evidences_edge(db_pool, mid2, thread_id)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            moment_ids = _fetch_thread_moment_ids(cur, thread_id)
            told_by_rows = _fetch_member_told_by(cur, moment_ids)

    result = _resolve_single_contributor(told_by_rows)
    assert result is None
