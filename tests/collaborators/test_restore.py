import os

import psycopg
import pytest

from flashback.collaborators.repository import (
    remove_collaborator_async,
    restore_collaborator_async,
)
from tests.collaborators.test_remove import _entity, _involves, _moment, _seed, _status

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


async def _call(fn, pid, user):
    conn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with conn.transaction():
            async with conn.cursor() as cur:
                return await fn(cur, person_id=pid, user_id=user)
    finally:
        await conn.close()


@db_only
async def test_remove_then_restore_round_trips():
    conn, cur, pid, X, Y = _seed()
    my = _moment(cur, pid, Y, "Y moment")
    e = _entity(cur, pid, Y, "OnlyY")
    _involves(cur, my, e)
    conn.close()

    await _call(remove_collaborator_async, pid, Y)
    res = await _call(restore_collaborator_async, pid, Y)
    assert res.moments_restored == 1
    assert res.entities_restored == 1

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (my,)); assert cur.fetchone()[0] == "active"
    cur.execute("SELECT status FROM entities WHERE id=%s", (e,)); assert cur.fetchone()[0] == "active"
    cur.execute(
        "SELECT status FROM collaborator_onboarding WHERE person_id=%s AND user_id=%s",
        (pid, Y),
    )
    assert cur.fetchone()[0] == "active"
    conn.close()


@db_only
async def test_restore_re_supersedes_resurrected_predecessor():
    conn, cur, pid, X, Y = _seed()
    m2 = _moment(cur, pid, Y, "Y six")
    m1 = _moment(cur, pid, X, "X six", status="superseded", superseded_by=m2)
    conn.close()

    await _call(remove_collaborator_async, pid, Y)   # m2->removed, m1 resurrected->active
    res = await _call(restore_collaborator_async, pid, Y)
    assert res.moments_re_superseded == 1

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (m2,)); assert cur.fetchone()[0] == "active"
    cur.execute("SELECT status FROM moments WHERE id=%s", (m1,)); assert cur.fetchone()[0] == "superseded"
    conn.close()


@db_only
async def test_restore_re_supersedes_buried_ancestor_3hop():
    # M0(X) <- M1(Y) <- M2(Y). remove(Y) resurrects the buried M0(X) (recursing
    # through M1(Y)); restore(Y) must re-supersede M0 so the round-trip is exact.
    conn, cur, pid, X, Y = _seed()
    m2 = _moment(cur, pid, Y, "M2", status="active")
    m1 = _moment(cur, pid, Y, "M1", status="superseded", superseded_by=m2)
    m0 = _moment(cur, pid, X, "M0", status="superseded", superseded_by=m1)
    conn.close()

    await _call(remove_collaborator_async, pid, Y)
    assert _status(m0) == "active"  # buried ancestor resurrected on remove

    await _call(restore_collaborator_async, pid, Y)
    assert _status(m2) == "active"       # restored
    assert _status(m0) == "superseded"   # re-superseded — exact inverse


@db_only
async def test_restore_when_active_is_noop():
    conn, cur, pid, X, Y = _seed()
    _moment(cur, pid, Y, "Y moment")
    conn.close()
    res = await _call(restore_collaborator_async, pid, Y)
    assert res.moments_restored == 0
