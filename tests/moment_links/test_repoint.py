import os

import psycopg
import pytest

from flashback.moment_links import insert_same_event_link
from flashback.moment_links.repository import repoint_records_on_supersession

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _setup():
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    pid = cur.fetchone()[0]
    ids = []
    for t in ("A", "B", "C"):
        cur.execute(
            "INSERT INTO moments (person_id, title, narrative, status) "
            "VALUES (%s, %s, 'n', 'active') RETURNING id::text",
            (pid, t),
        )
        ids.append(cur.fetchone()[0])
    return conn, cur, pid, ids  # A, B, C


@db_only
def test_repoint_substitutes_old_for_new():
    conn, cur, pid, (mA, mB, mC) = _setup()
    try:
        lid = insert_same_event_link(
            cur, person_id=pid, moment_a_id=mA, moment_b_id=mB, reason="r"
        )
        # B is superseded by C: link should now reference A & C.
        repoint_records_on_supersession(cur, old_id=mB, new_id=mC)
        cur.execute(
            "SELECT moment_a_id::text, moment_b_id::text, status "
            "FROM moment_same_event_links WHERE id = %s",
            (lid,),
        )
        a, b, status = cur.fetchone()
    finally:
        conn.close()
    assert status == "active"
    assert {a, b} == {mA, mC}


@db_only
def test_repoint_collapses_self_pair():
    conn, cur, pid, (mA, mB, mC) = _setup()
    try:
        lid = insert_same_event_link(
            cur, person_id=pid, moment_a_id=mA, moment_b_id=mB, reason="r"
        )
        # A is superseded by B -> link would become B&B -> collapse to unlinked.
        repoint_records_on_supersession(cur, old_id=mA, new_id=mB)
        cur.execute(
            "SELECT status FROM moment_same_event_links WHERE id = %s", (lid,)
        )
        (status,) = cur.fetchone()
    finally:
        conn.close()
    assert status == "unlinked"
