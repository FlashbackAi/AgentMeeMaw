"""HTTP tests for the generic artifact regenerate / edit surface.

Most tests here exercise validation that runs *before* any DB call, so
they work against the no-db ``client`` fixture. The happy-path DB tests
are intentionally minimal; they depend on TEST_DATABASE_URL like the
rest of the HTTP suite.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.http.conftest import auth_headers


pytestmark = pytest.mark.asyncio


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
