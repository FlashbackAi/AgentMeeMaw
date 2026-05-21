"""Profile-picture endpoint tests.

Auth and validation tests use the fast ``client`` fixture (no DB needed).
Happy-path and 404 tests use ``client_with_db`` (requires TEST_DATABASE_URL).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.http.conftest import FakeProfilePictureQueue, auth_headers


# --- helpers ----------------------------------------------------------------


async def _insert_person(pool, *, name="Test Subject", relationship="mother", gender=None):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO persons (name, relationship, gender) VALUES (%s, %s, %s) RETURNING id",
                (name, relationship, gender),
            )
            (pid,) = await cur.fetchone()
        await conn.commit()
    return str(pid)


# --- Regenerate: auth -------------------------------------------------------


class TestRegenerateAuth:
    async def test_missing_token_is_401(self, client):
        resp = await client.post(f"/persons/{uuid4()}/profile-picture", json={})
        assert resp.status_code == 401

    async def test_wrong_token_is_401(self, client):
        resp = await client.post(
            f"/persons/{uuid4()}/profile-picture",
            headers={"X-Service-Token": "wrong"},
            json={},
        )
        assert resp.status_code == 401


# --- Edit: auth + validation ------------------------------------------------


class TestEditAuth:
    async def test_missing_token_is_401(self, client):
        resp = await client.post(f"/persons/{uuid4()}/profile-picture/edit", json={})
        assert resp.status_code == 401


class TestEditValidation:
    async def test_missing_instructions_is_422(self, client):
        resp = await client.post(
            f"/persons/{uuid4()}/profile-picture/edit",
            headers=auth_headers(),
            json={},
        )
        assert resp.status_code == 422

    async def test_blank_instructions_is_422(self, client):
        resp = await client.post(
            f"/persons/{uuid4()}/profile-picture/edit",
            headers=auth_headers(),
            json={"instructions": "   "},
        )
        assert resp.status_code == 422

    async def test_oversized_instructions_is_422(self, client):
        resp = await client.post(
            f"/persons/{uuid4()}/profile-picture/edit",
            headers=auth_headers(),
            json={"instructions": "x" * 501},
        )
        assert resp.status_code == 422


# --- DB-touching: regenerate happy path ------------------------------------


class TestRegenerate:
    async def test_no_reference_enqueues_job(
        self, client_with_db, async_db_pool, fake_profile_picture_queue: FakeProfilePictureQueue
    ):
        pid = await _insert_person(async_db_pool, name="Sunita Rao", relationship="aunt", gender="she")

        resp = await client_with_db.post(
            f"/persons/{pid}/profile-picture",
            headers=auth_headers(),
            json={},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["person_id"] == pid
        assert body["mode"] == "no_reference"
        assert body["source"] == "regenerate"
        assert body["enqueued"] is True

        assert len(fake_profile_picture_queue.calls) == 1
        call = fake_profile_picture_queue.calls[0]
        assert call["name"] == "Sunita Rao"
        assert call["gender"] == "female"
        assert call["mode"] == "no_reference"
        assert call["source"] == "regenerate"
        assert "Pixar-style" in call["image_prompt"]
        assert call["reference_s3_key"] is None

    async def test_with_reference_sets_mode(
        self, client_with_db, async_db_pool, fake_profile_picture_queue: FakeProfilePictureQueue
    ):
        pid = await _insert_person(async_db_pool, name="Raj Kumar", relationship="father", gender="he")

        resp = await client_with_db.post(
            f"/persons/{pid}/profile-picture",
            headers=auth_headers(),
            json={"reference_s3_key": "uploads/abc123.jpg"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "with_reference"
        assert body["enqueued"] is True

        call = fake_profile_picture_queue.calls[0]
        assert call["reference_s3_key"] == "uploads/abc123.jpg"
        assert call["mode"] == "with_reference"

    async def test_unknown_person_is_404(self, client_with_db):
        resp = await client_with_db.post(
            f"/persons/{uuid4()}/profile-picture",
            headers=auth_headers(),
            json={},
        )
        assert resp.status_code == 404

    async def test_no_queue_returns_enqueued_false(
        self, app_with_db, async_db_pool
    ):
        import httpx

        app_with_db.state.profile_picture_queue = None
        pid = await _insert_person(async_db_pool, name="Leila Hassan", relationship="sister")
        transport = httpx.ASGITransport(app=app_with_db)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/persons/{pid}/profile-picture",
                headers=auth_headers(),
                json={},
            )
        assert resp.status_code == 200
        assert resp.json()["enqueued"] is False


# --- DB-touching: edit happy path ------------------------------------------


class TestEdit:
    async def test_edit_enqueues_with_instructions(
        self, client_with_db, async_db_pool, fake_profile_picture_queue: FakeProfilePictureQueue
    ):
        pid = await _insert_person(async_db_pool, name="Maya Patel", relationship="daughter", gender="she")

        resp = await client_with_db.post(
            f"/persons/{pid}/profile-picture/edit",
            headers=auth_headers(),
            json={"instructions": "wearing a red sari"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "edit"
        assert body["enqueued"] is True

        call = fake_profile_picture_queue.calls[0]
        assert call["source"] == "edit"
        assert call["user_prompt"] == "wearing a red sari"
        assert "wearing a red sari" in call["image_prompt"]

    async def test_edit_unknown_person_is_404(self, client_with_db):
        resp = await client_with_db.post(
            f"/persons/{uuid4()}/profile-picture/edit",
            headers=auth_headers(),
            json={"instructions": "smiling"},
        )
        assert resp.status_code == 404

    async def test_each_job_gets_unique_job_id(
        self, client_with_db, async_db_pool, fake_profile_picture_queue: FakeProfilePictureQueue
    ):
        pid = await _insert_person(async_db_pool, name="Omar Khalid", relationship="grandfather", gender="he")

        r1 = await client_with_db.post(
            f"/persons/{pid}/profile-picture",
            headers=auth_headers(),
            json={},
        )
        r2 = await client_with_db.post(
            f"/persons/{pid}/profile-picture",
            headers=auth_headers(),
            json={},
        )
        assert r1.json()["job_id"] != r2.json()["job_id"]
