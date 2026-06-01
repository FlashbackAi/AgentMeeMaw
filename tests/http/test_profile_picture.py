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


async def _read_latest_context(pool, *, person_id) -> dict:
    """Read persons.latest_generation_context. Returns {} if NULL."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT latest_generation_context FROM persons WHERE id = %s",
                (str(person_id),),
            )
            row = await cur.fetchone()
    return dict(row[0]) if row and row[0] else {}


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

        # Trigger payload: only job identifiers. CLAUDE.md §3.
        assert len(fake_profile_picture_queue.calls) == 1
        call = fake_profile_picture_queue.calls[0]
        assert call["source"] == "regenerate"
        assert call["composed_at"]
        assert "image_prompt" not in call  # confirm legacy field is gone

        # Prompt + mode + reference live on persons.latest_generation_context.
        context = await _read_latest_context(async_db_pool, person_id=pid)
        assert context["source"] == "regenerate"
        assert context["mode"] == "no_reference"
        assert context["reference_s3_key"] is None
        assert "Painterly semi-realistic portrait" in context["prompt"]
        assert "Red Dead Redemption 2" in context["prompt"]
        assert context["composed_at"] == call["composed_at"]

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

        context = await _read_latest_context(async_db_pool, person_id=pid)
        assert context["mode"] == "with_reference"
        assert context["reference_s3_key"] == "uploads/abc123.jpg"

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
        # Prompt content lives on the Postgres row.
        context = await _read_latest_context(async_db_pool, person_id=pid)
        assert "wearing a red sari" in context["prompt"]
        assert context["source"] == "edit"

    async def test_edit_stacks_prior_instructions_in_prompt_order(
        self, client_with_db, async_db_pool, fake_profile_picture_queue: FakeProfilePictureQueue
    ):
        pid = await _insert_person(async_db_pool, name="Arjun Mehta", relationship="father", gender="he")

        resp = await client_with_db.post(
            f"/persons/{pid}/profile-picture/edit",
            headers=auth_headers(),
            json={
                "instructions": "and a Rolls Royce in the background",
                "prior_instructions": ["he has round glasses"],
            },
        )
        assert resp.status_code == 200, resp.text

        context = await _read_latest_context(async_db_pool, person_id=pid)
        prompt = context["prompt"]
        glasses_idx = prompt.index("he has round glasses")
        rolls_idx = prompt.index("and a Rolls Royce in the background")
        assert glasses_idx < rolls_idx

    async def test_edit_drops_blank_prior_entries(
        self, client_with_db, async_db_pool, fake_profile_picture_queue: FakeProfilePictureQueue
    ):
        pid = await _insert_person(async_db_pool, name="Lina Park", relationship="aunt", gender="she")

        resp = await client_with_db.post(
            f"/persons/{pid}/profile-picture/edit",
            headers=auth_headers(),
            json={
                "instructions": "carrying a wicker basket",
                "prior_instructions": ["", "  ", "wearing a sun hat"],
            },
        )
        assert resp.status_code == 200, resp.text
        context = await _read_latest_context(async_db_pool, person_id=pid)
        # Both the surviving prior entry and the new edit land in the prompt;
        # the blank entries are skipped (no stray commas).
        assert "wearing a sun hat" in context["prompt"]
        assert "carrying a wicker basket" in context["prompt"]
        assert ",," not in context["prompt"]

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
