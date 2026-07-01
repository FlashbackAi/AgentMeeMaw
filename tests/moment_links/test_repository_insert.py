import os

import psycopg
import pytest

from flashback.moment_links import (
    canonical_pair,
    insert_contradiction,
    insert_same_event_link,
)

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def test_canonical_pair_orders_by_string():
    a, b = "ffff", "0000"
    assert canonical_pair(a, b) == ("0000", "ffff")
    assert canonical_pair(b, a) == ("0000", "ffff")


def _sync_person_and_moments():
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    pid = cur.fetchone()[0]
    ids = []
    for t in ("A", "B"):
        cur.execute(
            "INSERT INTO moments (person_id, title, narrative, status) "
            "VALUES (%s, %s, 'n', 'active') RETURNING id::text",
            (pid, t),
        )
        ids.append(cur.fetchone()[0])
    return conn, cur, pid, ids[0], ids[1]


@db_only
def test_insert_same_event_link_is_idempotent():
    conn, cur, pid, m1, m2 = _sync_person_and_moments()
    try:
        first = insert_same_event_link(
            cur, person_id=pid, moment_a_id=m1, moment_b_id=m2, reason="same day"
        )
        # Mirror order, second time -> conflict on the active partial index.
        second = insert_same_event_link(
            cur, person_id=pid, moment_a_id=m2, moment_b_id=m1, reason="x"
        )
    finally:
        conn.close()
    assert first is not None
    assert second is None


@db_only
def test_insert_contradiction_writes_pending():
    conn, cur, pid, m1, m2 = _sync_person_and_moments()
    try:
        cid = insert_contradiction(
            cur, person_id=pid, moment_a_id=m1, moment_b_id=m2, reason="age clash"
        )
        cur.execute(
            "SELECT status FROM moment_contradictions WHERE id = %s", (cid,)
        )
        (status,) = cur.fetchone()
    finally:
        conn.close()
    assert cid is not None
    assert status == "pending"
