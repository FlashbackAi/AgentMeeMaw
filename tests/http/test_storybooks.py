"""HTTP validation tests for the on-demand storybook endpoints.

These exercise validation that runs before any DB call (pydantic body
validation, auth), so they work against the no-db ``client`` fixture.
Happy-path generation is covered by the DB-backed generation suite, and the
collection/eligibility contract by ``test_storybook_collections.py`` and
``test_storybook_preview_route.py``.

The old ``preset`` and ``scope`` fields are gone -- the Python-render rework
replaced free-form scoping with the six fixed collections, and all three
request models are ``extra='forbid'``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.http.conftest import auth_headers

pytestmark = pytest.mark.asyncio


def render_urls() -> dict:
    """The presigned-URL set every render request must carry."""
    return {
        "pdf_put_url": "https://s3.example/put/book.pdf",
        "cover_put_url": "https://s3.example/put/cover.png",
        "page_put_urls": [f"https://s3.example/put/p{i}.png" for i in range(7)],
    }


class TestCreateStorybook:
    async def test_requires_auth(self, client):
        resp = await client.post("/storybooks", json={"person_id": str(uuid4())})
        assert resp.status_code == 401

    async def test_unknown_field_is_422(self, client):
        resp = await client.post(
            "/storybooks",
            headers=auth_headers(),
            json={
                "person_id": str(uuid4()),
                "storybook_id": str(uuid4()),
                "collection": "wisdom",
                **render_urls(),
                "preset": "not_a_real_preset",
            },
        )
        assert resp.status_code == 422

    async def test_missing_collection_is_422(self, client):
        resp = await client.post(
            "/storybooks",
            headers=auth_headers(),
            json={
                "person_id": str(uuid4()),
                "storybook_id": str(uuid4()),
                **render_urls(),
            },
        )
        assert resp.status_code == 422

    async def test_missing_render_urls_is_422(self, client):
        resp = await client.post(
            "/storybooks",
            headers=auth_headers(),
            json={
                "person_id": str(uuid4()),
                "storybook_id": str(uuid4()),
                "collection": "wisdom",
            },
        )
        assert resp.status_code == 422

    async def test_accepts_collection_and_optional_moment_ids(self):
        # Valid body shape parses cleanly (DB execution is covered elsewhere).
        from flashback.http.models import StorybookGenerateRequest

        moment_ids = [str(uuid4()), str(uuid4())]
        req = StorybookGenerateRequest.model_validate(
            {
                "person_id": str(uuid4()),
                "storybook_id": str(uuid4()),
                "collection": "wisdom",
                "moment_ids": moment_ids,
                **render_urls(),
            }
        )
        assert req.collection == "wisdom"
        assert req.moment_ids is not None
        assert [str(m) for m in req.moment_ids] == moment_ids

    async def test_moment_ids_is_optional(self):
        """Absent moment_ids means "auto-curate from the tags"."""
        from flashback.http.models import StorybookGenerateRequest

        req = StorybookGenerateRequest.model_validate(
            {
                "person_id": str(uuid4()),
                "storybook_id": str(uuid4()),
                "collection": "wisdom",
                **render_urls(),
            }
        )
        assert req.moment_ids is None


class TestRegenerateStorybook:
    async def test_unknown_field_is_422(self, client):
        resp = await client.post(
            f"/storybooks/{uuid4()}/regenerate",
            headers=auth_headers(),
            json={
                "person_id": str(uuid4()),
                **render_urls(),
                "preset": "garbage",
            },
        )
        assert resp.status_code == 422

    async def test_missing_render_urls_is_422(self, client):
        resp = await client.post(
            f"/storybooks/{uuid4()}/regenerate",
            headers=auth_headers(),
            json={"person_id": str(uuid4())},
        )
        assert resp.status_code == 422

    async def test_empty_page_put_urls_is_422(self, client):
        urls = render_urls() | {"page_put_urls": []}
        resp = await client.post(
            f"/storybooks/{uuid4()}/regenerate",
            headers=auth_headers(),
            json={"person_id": str(uuid4()), **urls},
        )
        assert resp.status_code == 422


class TestEditStorybook:
    async def test_missing_instructions_is_422(self, client):
        resp = await client.post(
            f"/storybooks/{uuid4()}/edit",
            headers=auth_headers(),
            json={"person_id": str(uuid4()), **render_urls()},
        )
        assert resp.status_code == 422

    async def test_blank_instructions_is_422(self, client):
        resp = await client.post(
            f"/storybooks/{uuid4()}/edit",
            headers=auth_headers(),
            json={
                "person_id": str(uuid4()),
                "instructions": "   ",
                **render_urls(),
            },
        )
        assert resp.status_code == 422

    async def test_unknown_field_is_422(self, client):
        resp = await client.post(
            f"/storybooks/{uuid4()}/edit",
            headers=auth_headers(),
            json={
                "person_id": str(uuid4()),
                "instructions": "make it warmer",
                **render_urls(),
                "preset": "garbage",
            },
        )
        assert resp.status_code == 422
