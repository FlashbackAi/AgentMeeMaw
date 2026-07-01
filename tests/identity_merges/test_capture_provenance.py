import os
import uuid

import psycopg
import pytest

from flashback.identity_merges.repository import auto_merge_async

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
async def test_auto_merge_stores_both_told_by():
    priya, ravi = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('S') RETURNING id::text")
    pid = cur.fetchone()[0]

    def ent(name, tb, ts):
        cur.execute(
            "INSERT INTO entities (person_id, kind, name, status, told_by_user_id, created_at) "
            "VALUES (%s,'person',%s,'active',%s,%s) RETURNING id::text",
            (pid, name, tb, ts),
        )
        return cur.fetchone()[0]

    src = ent("Amma", priya, "2026-01-01")
    tgt = ent("Amma", ravi, "2026-03-01")
    conn.close()

    aconn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with aconn.transaction():
            async with aconn.cursor() as c:
                sug = await auto_merge_async(
                    c, person_id=pid, source_id=src, target_id=tgt,
                    proposed_alias=None, confidence="high", notification_text="x",
                    push_embedding=None, embedding_model="m", embedding_model_version="v",
                    source_told_by_user_id=priya, target_told_by_user_id=ravi,
                )
    finally:
        await aconn.close()

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT source_told_by_user_id::text, target_told_by_user_id::text "
        "FROM identity_merge_suggestions WHERE id=%s",
        (sug,),
    )
    s, t = cur.fetchone()
    conn.close()
    assert s == priya and t == ravi


@db_only
async def test_scanner_suggestion_stores_both_told_by():
    """The 'ask' path (_insert_scanner_suggestion) also captures both told_by."""
    from flashback.identity_merges.scanner import (
        IdentityMergeCandidate,
        _insert_scanner_suggestion,
    )

    priya, ravi = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('S') RETURNING id::text")
    pid = cur.fetchone()[0]

    def ent(name, tb):
        cur.execute(
            "INSERT INTO entities (person_id, kind, name, status, told_by_user_id) "
            "VALUES (%s,'person',%s,'active',%s) RETURNING id::text",
            (pid, name, tb),
        )
        return cur.fetchone()[0]

    src = ent("Mom", priya)
    tgt = ent("Ishita", ravi)
    conn.close()

    candidate = IdentityMergeCandidate(
        person_id=pid, source_id=src, source_name="Mom", source_description="",
        source_aliases=[], target_id=tgt, target_name="Ishita",
        target_description="", target_aliases=[], kind="person",
        proposed_alias="Mom", reason_kind="same_name", embedding_distance=None,
        source_told_by_user_id=priya, target_told_by_user_id=ravi,
    )

    aconn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with aconn.transaction():
            async with aconn.cursor() as c:
                sug = await _insert_scanner_suggestion(
                    c, candidate=candidate, verifier_reason="same person", confidence="medium"
                )
    finally:
        await aconn.close()

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT source_told_by_user_id::text, target_told_by_user_id::text "
        "FROM identity_merge_suggestions WHERE id=%s",
        (sug,),
    )
    s, t = cur.fetchone()
    conn.close()
    assert s == priya and t == ravi
