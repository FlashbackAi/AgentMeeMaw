import os
import uuid

import psycopg
import pytest

from flashback.collaborators.repository import remove_collaborator_async

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _seed():
    """Person + two contributors (X, Y). Returns (conn, cur, pid, X, Y)."""
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    pid = cur.fetchone()[0]
    X, Y = str(uuid.uuid4()), str(uuid.uuid4())
    for u, name in ((X, "Xavier"), (Y, "Yusuf")):
        cur.execute(
            "INSERT INTO collaborator_onboarding "
            "(person_id, user_id, voice_anchor_text, voice_anchored_at, display_name, status) "
            "VALUES (%s, %s, 'rel', now(), %s, 'active')",
            (pid, u, name),
        )
    return conn, cur, pid, X, Y


def _moment(cur, pid, told_by, title, status="active", superseded_by=None):
    cur.execute(
        "INSERT INTO moments (person_id, title, narrative, status, told_by_user_id, told_by_display_name, superseded_by) "
        "VALUES (%s, %s, 'n', %s, %s, 'd', %s) RETURNING id::text",
        (pid, title, status, told_by, superseded_by),
    )
    return cur.fetchone()[0]


def _entity(cur, pid, told_by, name):
    cur.execute(
        "INSERT INTO entities (person_id, kind, name, status, told_by_user_id) "
        "VALUES (%s, 'person', %s, 'active', %s) RETURNING id::text",
        (pid, name, told_by),
    )
    return cur.fetchone()[0]


def _involves(cur, moment_id, entity_id):
    cur.execute(
        "INSERT INTO edges (from_kind, from_id, to_kind, to_id, edge_type, status) "
        "VALUES ('moment', %s, 'entity', %s, 'involves', 'active')",
        (moment_id, entity_id),
    )


async def _remove(pid, user):
    conn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with conn.transaction():
            async with conn.cursor() as cur:
                return await remove_collaborator_async(cur, person_id=pid, user_id=user)
    finally:
        await conn.close()


@db_only
async def test_remove_hides_moments_and_orphaned_entities():
    conn, cur, pid, X, Y = _seed()
    my = _moment(cur, pid, Y, "Y moment")
    e_orphan = _entity(cur, pid, Y, "OnlyY")
    e_shared = _entity(cur, pid, Y, "Shared")
    _involves(cur, my, e_orphan)
    _involves(cur, my, e_shared)
    mx = _moment(cur, pid, X, "X moment")
    _involves(cur, mx, e_shared)
    conn.close()

    result = await _remove(pid, Y)
    assert result.moments_removed == 1
    assert result.entities_removed == 1  # only OnlyY

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (my,)); assert cur.fetchone()[0] == "removed"
    cur.execute("SELECT status FROM entities WHERE id=%s", (e_orphan,)); assert cur.fetchone()[0] == "removed"
    cur.execute("SELECT status FROM entities WHERE id=%s", (e_shared,)); assert cur.fetchone()[0] == "active"
    conn.close()


@db_only
async def test_remove_resurrects_cross_contributor_superseded():
    conn, cur, pid, X, Y = _seed()
    m2 = _moment(cur, pid, Y, "Y winning six")
    m1 = _moment(cur, pid, X, "X winning six", status="superseded", superseded_by=m2)
    conn.close()

    result = await _remove(pid, Y)
    assert result.moments_removed == 1
    assert result.moments_resurrected == 1

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (m2,)); assert cur.fetchone()[0] == "removed"
    cur.execute("SELECT status, superseded_by::text FROM moments WHERE id=%s", (m1,))
    st, sup = cur.fetchone()
    assert st == "active"
    assert sup == m2
    conn.close()


def _status(mid):
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (mid,))
    s = cur.fetchone()[0]
    conn.close()
    return s


@db_only
async def test_remove_resurrects_only_nearest_surviving_ancestor():
    # M0(X) <- M1(Z) <- M2(Y); removing Y resurrects only M1(Z), never M0(X).
    conn, cur, pid, X, Y = _seed()
    Z = str(uuid.uuid4())
    m2 = _moment(cur, pid, Y, "M2", status="active")
    m1 = _moment(cur, pid, Z, "M1", status="superseded", superseded_by=m2)
    m0 = _moment(cur, pid, X, "M0", status="superseded", superseded_by=m1)
    conn.close()

    result = await _remove(pid, Y)
    assert result.moments_resurrected == 1
    assert _status(m2) == "removed"
    assert _status(m1) == "active"        # nearest surviving ancestor
    assert _status(m0) == "superseded"    # buried ancestor stays put


@db_only
async def test_remove_same_contributor_chain_resurrects_nothing():
    # M0(Y) <- M1(Y) <- M2(Y); all one voice — nothing to resurrect.
    conn, cur, pid, X, Y = _seed()
    m2 = _moment(cur, pid, Y, "M2", status="active")
    m1 = _moment(cur, pid, Y, "M1", status="superseded", superseded_by=m2)
    m0 = _moment(cur, pid, Y, "M0", status="superseded", superseded_by=m1)
    conn.close()

    result = await _remove(pid, Y)
    assert result.moments_resurrected == 0
    assert _status(m2) == "removed"
    assert _status(m1) == "superseded"
    assert _status(m0) == "superseded"


@db_only
async def test_remove_is_idempotent():
    conn, cur, pid, X, Y = _seed()
    _moment(cur, pid, Y, "Y moment")
    conn.close()
    first = await _remove(pid, Y)
    assert first.moments_removed == 1
    second = await _remove(pid, Y)
    assert second.moments_removed == 0
    assert second.entities_removed == 0
