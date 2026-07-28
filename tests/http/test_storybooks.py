"""HTTP validation tests for the on-demand storybook endpoints.

These exercise validation that runs before any DB call (preset resolution,
pydantic body validation, auth), so they work against the no-db ``client``
fixture. Happy-path generation is covered by the DB-backed generation suite.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.http.conftest import auth_headers

pytestmark = pytest.mark.asyncio


class TestCreateStorybook:
    async def test_requires_auth(self, client):
        resp = await client.post("/storybooks", json={"person_id": str(uuid4())})
        assert resp.status_code == 401

    async def test_unknown_preset_is_400(self, client):
        resp = await client.post(
            "/storybooks",
            headers=auth_headers(),
            json={"person_id": str(uuid4()), "preset": "not_a_real_preset"},
        )
        assert resp.status_code == 400
        assert "preset" in resp.json()["detail"]

    async def test_unknown_scope_field_is_422(self, client):
        resp = await client.post(
            "/storybooks",
            headers=auth_headers(),
            json={"person_id": str(uuid4()), "scope": {"bogus": "x"}},
        )
        assert resp.status_code == 422

    async def test_accepts_theme_and_life_period_scope(self):
        # Valid body shape parses cleanly (DB execution is covered elsewhere).
        from flashback.http.models import StorybookGenerateRequest

        req = StorybookGenerateRequest.model_validate(
            {
                "person_id": str(uuid4()),
                "scope": {"theme_id": str(uuid4()), "life_period": "childhood"},
            }
        )
        assert req.scope is not None
        assert req.scope.life_period == "childhood"


class TestRegenerateStorybook:
    async def test_unknown_preset_is_400(self, client):
        resp = await client.post(
            f"/storybooks/{uuid4()}/regenerate",
            headers=auth_headers(),
            json={"person_id": str(uuid4()), "preset": "garbage"},
        )
        assert resp.status_code == 400

    async def test_too_many_tags_is_422(self, client):
        resp = await client.post(
            f"/storybooks/{uuid4()}/regenerate",
            headers=auth_headers(),
            json={
                "person_id": str(uuid4()),
                "tags": ["warmth", "grief", "love", "pride"],
            },
        )
        assert resp.status_code == 422


class TestEditStorybook:
    async def test_missing_instructions_is_422(self, client):
        resp = await client.post(
            f"/storybooks/{uuid4()}/edit",
            headers=auth_headers(),
            json={"person_id": str(uuid4())},
        )
        assert resp.status_code == 422

    async def test_blank_instructions_is_422(self, client):
        resp = await client.post(
            f"/storybooks/{uuid4()}/edit",
            headers=auth_headers(),
            json={"person_id": str(uuid4()), "instructions": "   "},
        )
        assert resp.status_code == 422

    async def test_unknown_preset_is_400(self, client):
        resp = await client.post(
            f"/storybooks/{uuid4()}/edit",
            headers=auth_headers(),
            json={
                "person_id": str(uuid4()),
                "instructions": "make it warmer",
                "preset": "garbage",
            },
        )
        assert resp.status_code == 400
