import os
import uuid

import psycopg
import pytest

from flashback.identity_merges.repository import auto_merge_async, unmerge_async

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _person(cur):
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    return cur.fetchone()[0]


def _entity(cur, pid, name, told_by, created_at):
    cur.execute(
        "INSERT INTO entities (person_id, kind, name, description, aliases, status, "
        "told_by_user_id, created_at) "
        "VALUES (%s, 'person', %s, 'd', '{}', 'active', %s, %s) RETURNING id::text",
        (pid, name, told_by, created_at),
    )
    return cur.fetchone()[0]


async def _auto_merge(pid, source_id, target_id):
    conn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with conn.transaction():
            async with conn.cursor() as cur:
                return await auto_merge_async(
                    cur, person_id=pid, source_id=source_id, target_id=target_id,
                    proposed_alias=None, confidence="high",
                    notification_text="same person",
                    push_embedding=None, embedding_model="m", embedding_model_version="v",
                )
    finally:
        await conn.close()


@db_only
async def test_survivor_takes_older_entitys_told_by():
    priya, ravi = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    pid = _person(cur)
    older = _entity(cur, pid, "Amma", priya, "2026-01-01")
    newer = _entity(cur, pid, "Amma", ravi, "2026-03-01")
    conn.close()

    await _auto_merge(pid, source_id=older, target_id=newer)

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT told_by_user_id::text, status FROM entities WHERE id=%s", (newer,))
    survivor_told_by, status = cur.fetchone()
    conn.close()
    assert status == "active"
    assert survivor_told_by == priya  # earliest introducer wins


@db_only
async def test_survivor_keeps_own_when_older_is_the_target():
    # Converse orientation: the OLDER entity is passed as target (survivor).
    # Survivor must keep its own told_by — no rewrite.
    priya, ravi = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    pid = _person(cur)
    older = _entity(cur, pid, "Amma", priya, "2026-01-01")  # target / survivor
    newer = _entity(cur, pid, "Amma", ravi, "2026-03-01")  # source
    conn.close()

    await _auto_merge(pid, source_id=newer, target_id=older)

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT told_by_user_id::text FROM entities WHERE id=%s", (older,))
    assert cur.fetchone()[0] == priya  # older survivor keeps its own
    conn.close()


@db_only
async def test_creator_era_null_older_yields_null_survivor():
    ravi = str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    pid = _person(cur)
    older = _entity(cur, pid, "Amma", None, "2026-01-01")  # creator-era NULL, source
    newer = _entity(cur, pid, "Amma", ravi, "2026-03-01")  # survivor / target
    conn.close()

    await _auto_merge(pid, source_id=older, target_id=newer)

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT told_by_user_id FROM entities WHERE id=%s", (newer,))
    assert cur.fetchone()[0] is None  # survivor adopts the older creator-era NULL
    conn.close()


@db_only
async def test_unmerge_restores_both_told_by():
    priya, ravi = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    pid = _person(cur)
    older = _entity(cur, pid, "Amma", priya, "2026-01-01")  # source
    newer = _entity(cur, pid, "Amma", ravi, "2026-03-01")  # survivor
    conn.close()
    sug_id = await _auto_merge(pid, source_id=older, target_id=newer)

    conn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with conn.transaction():
            async with conn.cursor() as cur:
                res = await unmerge_async(
                    cur, suggestion_id=sug_id, push_embedding=None,
                    embedding_model="m", embedding_model_version="v",
                )
    finally:
        await conn.close()
    assert res is not None

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT told_by_user_id::text FROM entities WHERE id=%s", (newer,))
    assert cur.fetchone()[0] == ravi  # survivor reverts to pre-merge
    cur.execute(
        "SELECT told_by_user_id::text FROM entities WHERE id=%s::uuid",
        (str(res.resurrected_entity_id),),
    )
    assert cur.fetchone()[0] == priya  # resurrected source keeps its own
    conn.close()
