import os
from uuid import uuid4

import psycopg
import pytest

from flashback.moment_links import (
    insert_contradiction,
    insert_same_event_link,
)
from flashback.moment_links.repository import (
    acknowledge_event_link_async,
    dismiss_contradiction_async,
    list_contradictions_async,
    list_event_links_async,
    unlink_event_link_async,
)

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _seed_sync():
    """Seed a person, a Ravi collaborator, two moments (one creator-era, one
    told_by Ravi). Returns (pid, mA, mB). Uses a sync autocommit connection so
    the rows are visible to the async pool used for the read assertions."""
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    pid = cur.fetchone()[0]
    ravi = str(uuid4())
    cur.execute(
        "INSERT INTO collaborator_onboarding "
        "(person_id, user_id, voice_anchor_text, voice_anchored_at, display_name, status) "
        "VALUES (%s, %s, 'his son', now(), 'Ravi', 'active')",
        (pid, ravi),
    )
    mids = []
    for t, tb in (("A", None), ("B", ravi)):
        cur.execute(
            "INSERT INTO moments "
            "(person_id, title, narrative, status, told_by_user_id, told_by_display_name) "
            "VALUES (%s, %s, 'n', 'active', %s, %s) RETURNING id::text",
            (pid, t, tb, "Ravi" if tb else None),
        )
        mids.append(cur.fetchone()[0])
    conn.close()
    return pid, mids[0], mids[1], ravi


def _seed_link_sync(pid, m1, m2, reason="r"):
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    lid = insert_same_event_link(
        cur, person_id=pid, moment_a_id=m1, moment_b_id=m2, reason=reason
    )
    conn.close()
    return lid


def _seed_contradiction_sync(pid, m1, m2, reason="clash"):
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cid = insert_contradiction(
        cur, person_id=pid, moment_a_id=m1, moment_b_id=m2, reason=reason
    )
    conn.close()
    return cid


@db_only
async def test_list_event_links_resolves_live_provenance(async_db_pool):
    pid, mA, mB, _ravi = _seed_sync()
    _seed_link_sync(pid, mA, mB, reason="same day")
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            links = await list_event_links_async(cur, person_id=pid)
    assert len(links) == 1
    titles = {links[0].moment_a_title, links[0].moment_b_title}
    assert titles == {"A", "B"}
    names = {links[0].told_by_a_display_name, links[0].told_by_b_display_name}
    assert "Ravi" in names
    assert None in names  # creator-era side has no display name


@db_only
async def test_acknowledge_and_unlink(async_db_pool):
    pid, mA, mB, _ravi = _seed_sync()
    lid = _seed_link_sync(pid, mA, mB)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            assert await acknowledge_event_link_async(cur, link_id=lid) is True
            assert await unlink_event_link_async(cur, link_id=lid) is True
            await conn.commit()
        async with conn.cursor() as cur:
            both = await list_event_links_async(
                cur, person_id=pid, include_acknowledged=True
            )
    assert both == []  # unlinked links never appear


@db_only
async def test_list_and_dismiss_contradiction(async_db_pool):
    pid, mA, mB, _ravi = _seed_sync()
    cid = _seed_contradiction_sync(pid, mA, mB)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            items = await list_contradictions_async(cur, person_id=pid)
            assert len(items) == 1
            assert await dismiss_contradiction_async(cur, item_id=cid) is True
            await conn.commit()
        async with conn.cursor() as cur:
            assert await list_contradictions_async(cur, person_id=pid) == []
