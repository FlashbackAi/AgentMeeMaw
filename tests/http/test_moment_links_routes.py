"""SP5 endpoints: /event_links and /contradictions (DB-gated)."""

import os

import psycopg
import pytest

from tests.http.conftest import auth_headers

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _seed_person_and_moments():
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
    conn.close()
    return pid, ids[0], ids[1]


def _seed_link(pid, a, b):
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO moment_same_event_links (person_id, moment_a_id, moment_b_id, reason) "
        "VALUES (%s, %s, %s, 'r') RETURNING id::text",
        (pid, a, b),
    )
    lid = cur.fetchone()[0]
    conn.close()
    return lid


def _seed_contradiction(pid, a, b):
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO moment_contradictions (person_id, moment_a_id, moment_b_id, reason) "
        "VALUES (%s, %s, %s, 'clash') RETURNING id::text",
        (pid, a, b),
    )
    cid = cur.fetchone()[0]
    conn.close()
    return cid


@db_only
async def test_list_event_links(client_with_db):
    pid, mA, mB = _seed_person_and_moments()
    _seed_link(pid, mA, mB)
    resp = await client_with_db.get(
        "/event_links", params={"person_id": pid}, headers=auth_headers()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert {body[0]["moment_a_title"], body[0]["moment_b_title"]} == {"A", "B"}


@db_only
async def test_acknowledge_then_absent_from_feed(client_with_db):
    pid, mA, mB = _seed_person_and_moments()
    lid = _seed_link(pid, mA, mB)
    ack = await client_with_db.post(
        f"/event_links/{lid}/acknowledge", headers=auth_headers()
    )
    assert ack.status_code == 200
    resp = await client_with_db.get(
        "/event_links", params={"person_id": pid}, headers=auth_headers()
    )
    assert resp.json() == []  # default feed = unacknowledged only


@db_only
async def test_unlink_then_404_on_second_call(client_with_db):
    pid, mA, mB = _seed_person_and_moments()
    lid = _seed_link(pid, mA, mB)
    first = await client_with_db.post(
        f"/event_links/{lid}/unlink", headers=auth_headers()
    )
    assert first.status_code == 200
    second = await client_with_db.post(
        f"/event_links/{lid}/unlink", headers=auth_headers()
    )
    assert second.status_code == 404


@db_only
async def test_list_and_dismiss_contradiction(client_with_db):
    pid, mA, mB = _seed_person_and_moments()
    cid = _seed_contradiction(pid, mA, mB)
    listed = await client_with_db.get(
        "/contradictions", params={"person_id": pid}, headers=auth_headers()
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    dismiss = await client_with_db.post(
        f"/contradictions/{cid}/dismiss", headers=auth_headers()
    )
    assert dismiss.status_code == 200
    second = await client_with_db.post(
        f"/contradictions/{cid}/dismiss", headers=auth_headers()
    )
    assert second.status_code == 404
