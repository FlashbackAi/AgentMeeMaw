import os
import uuid

import psycopg
import pytest

from tests.http.conftest import auth_headers

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _seed_person_user_moment():
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
    pid = cur.fetchone()[0]
    user = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO collaborator_onboarding "
        "(person_id, user_id, voice_anchor_text, voice_anchored_at, display_name, status) "
        "VALUES (%s, %s, 'rel', now(), 'Y', 'active')",
        (pid, user),
    )
    cur.execute(
        "INSERT INTO moments (person_id, title, narrative, status, told_by_user_id, told_by_display_name) "
        "VALUES (%s, 'M', 'n', 'active', %s, 'Y') RETURNING id::text",
        (pid, user),
    )
    mid = cur.fetchone()[0]
    conn.close()
    return pid, user, mid


@db_only
async def test_remove_then_restore_endpoints(client_with_db):
    pid, user, mid = _seed_person_user_moment()

    r = await client_with_db.post(
        "/collaborators/remove",
        json={"person_id": pid, "user_id": user},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    assert r.json()["moments_removed"] == 1

    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT status FROM moments WHERE id=%s", (mid,)); assert cur.fetchone()[0] == "removed"
    conn.close()

    r2 = await client_with_db.post(
        "/collaborators/restore",
        json={"person_id": pid, "user_id": user},
        headers=auth_headers(),
    )
    assert r2.status_code == 200
    assert r2.json()["moments_restored"] == 1


@db_only
async def test_remove_unknown_user_is_zero_not_404(client_with_db):
    pid, _user, _mid = _seed_person_user_moment()
    r = await client_with_db.post(
        "/collaborators/remove",
        json={"person_id": pid, "user_id": str(uuid.uuid4())},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    assert r.json()["moments_removed"] == 0


@db_only
async def test_role_id_is_tolerated(client_with_db):
    pid, user, _mid = _seed_person_user_moment()
    r = await client_with_db.post(
        "/collaborators/remove",
        json={"person_id": pid, "user_id": user, "role_id": str(uuid.uuid4())},
        headers=auth_headers(),
    )
    assert r.status_code == 200
