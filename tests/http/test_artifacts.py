"""HTTP tests for the generic artifact regenerate / edit surface.

Most tests here exercise validation that runs *before* any DB call, so
they work against the no-db ``client`` fixture. The happy-path DB tests
are intentionally minimal; they depend on TEST_DATABASE_URL like the
rest of the HTTP suite.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from tests.http.conftest import auth_headers


pytestmark = pytest.mark.asyncio


# --- helpers (DB-touching gender-grounding tests) ---------------------------


async def _insert_person(pool, *, name="Test Subject", relationship="mother"):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO persons (name, relationship) VALUES (%s, %s) "
                "RETURNING id",
                (name, relationship),
            )
            (pid,) = await cur.fetchone()
        await conn.commit()
    return str(pid)


async def _insert_entity(
    pool,
    *,
    person_id: str,
    name: str,
    gender: str | None = None,
    generation_prompt: str = "A young man standing in a doorway.",
):
    attrs: dict[str, str] = {}
    if gender is not None:
        attrs["gender"] = gender
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO entities "
                "(person_id, kind, name, attributes, generation_prompt) "
                "VALUES (%s, 'person', %s, %s, %s) RETURNING id",
                (person_id, name, json.dumps(attrs), generation_prompt),
            )
            (eid,) = await cur.fetchone()
        await conn.commit()
    return str(eid)


async def _insert_thread(
    pool,
    *,
    person_id: str,
    name: str = "Sunday cricket",
    generation_prompt: str = "A dusty field at golden hour.",
):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO threads (person_id, name, description, generation_prompt) "
                "VALUES (%s, %s, 'd', %s) RETURNING id",
                (person_id, name, generation_prompt),
            )
            (tid,) = await cur.fetchone()
        await conn.commit()
    return str(tid)


async def _read_latest_context(pool, *, table: str, record_id: str) -> dict:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT latest_generation_context FROM {table} WHERE id = %s",
                (record_id,),
            )
            row = await cur.fetchone()
    return dict(row[0]) if row and row[0] else {}


class TestPresetsEndpoint:
    async def test_returns_default_first(self, client):
        resp = await client.get("/artifact-presets", headers=auth_headers())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "presets" in body
        assert body["presets"][0]["slug"] == "painterly_cinematic"
        assert body["presets"][0]["is_default"] is True

    async def test_returns_five_presets(self, client):
        resp = await client.get("/artifact-presets", headers=auth_headers())
        body = resp.json()
        assert len(body["presets"]) == 5
        slugs = {p["slug"] for p in body["presets"]}
        assert slugs == {
            "painterly_cinematic",
            "golden_hour",
            "twilight",
            "storybook",
            "vintage_film",
        }

    async def test_requires_auth(self, client):
        resp = await client.get("/artifact-presets")
        assert resp.status_code == 401


class TestRegenerateValidation:
    """Validation reachable without a DB pool (runs before the DB lookup)."""

    async def test_thread_with_reference_is_400(self, client):
        rid = uuid4()
        pid = uuid4()
        resp = await client.post(
            f"/artifacts/thread/{rid}/regenerate",
            headers=auth_headers(),
            json={"person_id": str(pid), "reference_s3_key": "uploads/k.jpg"},
        )
        assert resp.status_code == 400
        assert "reference_s3_key" in resp.json()["detail"]

    async def test_unknown_preset_is_400(self, client):
        rid = uuid4()
        pid = uuid4()
        resp = await client.post(
            f"/artifacts/moment/{rid}/regenerate",
            headers=auth_headers(),
            json={"person_id": str(pid), "preset": "not_a_real_preset"},
        )
        assert resp.status_code == 400
        assert "preset" in resp.json()["detail"]

    async def test_unsupported_record_type_is_422(self, client):
        """FastAPI enforces the Literal record_type at the path layer."""
        rid = uuid4()
        pid = uuid4()
        resp = await client.post(
            f"/artifacts/person/{rid}/regenerate",
            headers=auth_headers(),
            json={"person_id": str(pid)},
        )
        # Person is excluded from the generic surface (profile-picture has
        # its own endpoint), so FastAPI rejects the path.
        assert resp.status_code == 422

    async def test_moment_with_known_reference_validates_fine(self, client):
        """Moments accept reference_s3_key without 400.

        Sanity check that the threads-only restriction doesn't accidentally
        reject the allowed record types. Happy-path DB execution is covered
        by the DB-touching suite (skipped without TEST_DATABASE_URL).
        """
        rid = uuid4()
        pid = uuid4()
        # Build a request that passes every pre-DB validation. We don't
        # follow through to the DB call because db_pool=None on this
        # fixture; we just verify the request body is *accepted*.
        from flashback.http.models import ArtifactRegenerateRequest

        ArtifactRegenerateRequest.model_validate(
            {
                "person_id": str(pid),
                "preset": "golden_hour",
                "reference_s3_key": "uploads/house.jpg",
            }
        )


class TestEditValidation:
    async def test_thread_with_reference_is_400(self, client):
        rid = uuid4()
        pid = uuid4()
        resp = await client.post(
            f"/artifacts/thread/{rid}/edit",
            headers=auth_headers(),
            json={
                "person_id": str(pid),
                "instructions": "warmer light",
                "reference_s3_key": "uploads/k.jpg",
            },
        )
        assert resp.status_code == 400

    async def test_missing_instructions_is_422(self, client):
        rid = uuid4()
        pid = uuid4()
        resp = await client.post(
            f"/artifacts/moment/{rid}/edit",
            headers=auth_headers(),
            json={"person_id": str(pid)},
        )
        assert resp.status_code == 422

    async def test_blank_instructions_is_422(self, client):
        rid = uuid4()
        pid = uuid4()
        resp = await client.post(
            f"/artifacts/moment/{rid}/edit",
            headers=auth_headers(),
            json={"person_id": str(pid), "instructions": "   "},
        )
        assert resp.status_code == 422

    async def test_unknown_preset_is_400(self, client):
        rid = uuid4()
        pid = uuid4()
        resp = await client.post(
            f"/artifacts/entity/{rid}/edit",
            headers=auth_headers(),
            json={
                "person_id": str(pid),
                "instructions": "warmer light",
                "preset": "garbage",
            },
        )
        assert resp.status_code == 400


# --- DB-touching: entity's own gender grounds its portrait ------------------
#
# Task 8: an entity portrait (regenerate/edit) must ground the ENTITY's own
# stored attributes.gender, not just the subject/contributor. Threads and
# other record types are unaffected.


class TestEntityGenderGrounding:
    async def test_entity_regenerate_appends_entity_gender_clause(
        self, client_with_db, async_db_pool
    ):
        pid = await _insert_person(async_db_pool, name="Test Subject")
        eid = await _insert_entity(
            async_db_pool, person_id=pid, name="Aarav", gender="male"
        )

        resp = await client_with_db.post(
            f"/artifacts/entity/{eid}/regenerate",
            headers=auth_headers(),
            json={"person_id": pid},
        )
        assert resp.status_code == 200, resp.text

        context = await _read_latest_context(
            async_db_pool, table="entities", record_id=eid
        )
        assert "a man" in context["prompt"]
        assert "Aarav" in context["prompt"]

    async def test_entity_regenerate_female_gender_clause(
        self, client_with_db, async_db_pool
    ):
        pid = await _insert_person(async_db_pool, name="Test Subject")
        eid = await _insert_entity(
            async_db_pool, person_id=pid, name="Priya", gender="female"
        )

        resp = await client_with_db.post(
            f"/artifacts/entity/{eid}/regenerate",
            headers=auth_headers(),
            json={"person_id": pid},
        )
        assert resp.status_code == 200, resp.text

        context = await _read_latest_context(
            async_db_pool, table="entities", record_id=eid
        )
        assert "a woman" in context["prompt"]

    async def test_entity_with_no_stored_gender_adds_no_clause(
        self, client_with_db, async_db_pool
    ):
        pid = await _insert_person(async_db_pool, name="Test Subject")
        eid = await _insert_entity(
            async_db_pool,
            person_id=pid,
            name="Comet",
            gender=None,
            generation_prompt="An old dog resting by the porch steps.",
        )

        resp = await client_with_db.post(
            f"/artifacts/entity/{eid}/regenerate",
            headers=auth_headers(),
            json={"person_id": pid},
        )
        assert resp.status_code == 200, resp.text

        context = await _read_latest_context(
            async_db_pool, table="entities", record_id=eid
        )
        # No stored gender -> no invented clause, no crash.
        assert "a man" not in context["prompt"]
        assert "a woman" not in context["prompt"]

    async def test_entity_edit_also_appends_entity_gender_clause(
        self, client_with_db, async_db_pool
    ):
        pid = await _insert_person(async_db_pool, name="Test Subject")
        eid = await _insert_entity(
            async_db_pool, person_id=pid, name="Ishita", gender="female"
        )

        resp = await client_with_db.post(
            f"/artifacts/entity/{eid}/edit",
            headers=auth_headers(),
            json={"person_id": pid, "instructions": "warmer light"},
        )
        assert resp.status_code == 200, resp.text

        context = await _read_latest_context(
            async_db_pool, table="entities", record_id=eid
        )
        assert "a woman" in context["prompt"]

    async def test_thread_regenerate_gets_no_entity_gender_clause(
        self, client_with_db, async_db_pool
    ):
        """Threads are abstract arcs; unchanged behavior — no entity clause."""
        pid = await _insert_person(async_db_pool, name="Test Subject")
        tid = await _insert_thread(async_db_pool, person_id=pid)

        resp = await client_with_db.post(
            f"/artifacts/thread/{tid}/regenerate",
            headers=auth_headers(),
            json={"person_id": pid},
        )
        assert resp.status_code == 200, resp.text

        context = await _read_latest_context(
            async_db_pool, table="threads", record_id=tid
        )
        assert "a man" not in context["prompt"]
        assert "a woman" not in context["prompt"]
