import os
import uuid

import psycopg
import pytest

from flashback.identity_merges.repository import (
    auto_merge_async,
    list_auto_merged_async,
)

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
async def test_auto_merge_feed_exposes_cross_contributor_and_names():
    priya, ravi = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('S') RETURNING id::text")
    pid = cur.fetchone()[0]
    for u, nm in ((priya, "Priya"), (ravi, "Ravi")):
        cur.execute(
            "INSERT INTO collaborator_onboarding "
            "(person_id, user_id, voice_anchor_text, voice_anchored_at, display_name, status) "
            "VALUES (%s,%s,'rel',now(),%s,'active')",
            (pid, u, nm),
        )

    def ent(name, tb, ts):
        cur.execute(
            "INSERT INTO entities (person_id,kind,name,status,told_by_user_id,created_at) "
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
                await auto_merge_async(
                    c, person_id=pid, source_id=src, target_id=tgt, proposed_alias=None,
                    confidence="high", notification_text="x", push_embedding=None,
                    embedding_model="m", embedding_model_version="v",
                    source_told_by_user_id=priya, target_told_by_user_id=ravi,
                )
        async with aconn.cursor() as c:
            feed = await list_auto_merged_async(c, person_id=pid)
    finally:
        await aconn.close()

    assert len(feed) == 1
    item = feed[0]
    assert item.cross_contributor is True
    assert {item.source_told_by_display_name, item.target_told_by_display_name} == {"Priya", "Ravi"}


@db_only
async def test_same_contributor_merge_is_not_cross_contributor():
    keerthi = str(uuid.uuid4())
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('S') RETURNING id::text")
    pid = cur.fetchone()[0]

    def ent(name, ts):
        cur.execute(
            "INSERT INTO entities (person_id,kind,name,status,told_by_user_id,created_at) "
            "VALUES (%s,'person',%s,'active',%s,%s) RETURNING id::text",
            (pid, name, keerthi, ts),
        )
        return cur.fetchone()[0]

    src = ent("Raj", "2026-01-01")
    tgt = ent("Raj", "2026-03-01")
    conn.close()

    aconn = await psycopg.AsyncConnection.connect(_DB)
    try:
        async with aconn.transaction():
            async with aconn.cursor() as c:
                await auto_merge_async(
                    c, person_id=pid, source_id=src, target_id=tgt, proposed_alias=None,
                    confidence="high", notification_text="x", push_embedding=None,
                    embedding_model="m", embedding_model_version="v",
                    source_told_by_user_id=keerthi, target_told_by_user_id=keerthi,
                )
        async with aconn.cursor() as c:
            feed = await list_auto_merged_async(c, person_id=pid)
    finally:
        await aconn.close()

    assert feed[0].cross_contributor is False
