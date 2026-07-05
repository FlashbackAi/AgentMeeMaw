"""POST /storybooks/preview -- thin route over build_preview; assert the
error mapping and passthrough with the builder monkeypatched (the builder
itself is covered in tests/storybook/test_preview.py)."""

from __future__ import annotations

from uuid import uuid4

from flashback.http.routes import storybooks as storybooks_route
from flashback.llm.errors import LLMError
from flashback.storybook.generation import (
    StorybookNotFound,
    StorybookTooThin,
    UnknownCollection,
)

_HEADERS = {"X-Service-Token": "test-token"}


def _body() -> dict:
    return {"person_id": str(uuid4()), "collection": "childhood"}


async def test_preview_returns_builder_payload(client, monkeypatch) -> None:
    payload = {
        "collection": "childhood",
        "bounds": {"min_select": 5, "max_select": 25},
        "moments": [{
            "id": str(uuid4()), "title": "t", "snippet": "n",
            "life_period": "", "picked": True,
            "suggested_collection": "childhood", "used_in": [],
        }],
    }

    async def _fake(**_kwargs):
        return payload

    monkeypatch.setattr(storybooks_route, "build_preview", _fake)
    r = await client.post(
        "/storybooks/preview", json=_body(), headers=_HEADERS
    )
    assert r.status_code == 200
    assert r.json() == payload


async def test_preview_error_mapping(client, monkeypatch) -> None:
    cases = [
        (UnknownCollection("memoir"), 400),
        (StorybookNotFound("nope"), 404),
        (StorybookTooThin(2), 409),
        (LLMError("curation failed"), 502),
    ]

    def _raiser(exc):
        async def _raise(**_kwargs):
            raise exc

        return _raise

    for exc, expected in cases:
        monkeypatch.setattr(
            storybooks_route, "build_preview", _raiser(exc)
        )
        r = await client.post(
            "/storybooks/preview", json=_body(), headers=_HEADERS
        )
        assert r.status_code == expected, (exc, r.status_code)


async def test_preview_requires_service_token(client) -> None:
    r = await client.post("/storybooks/preview", json=_body())
    assert r.status_code in (401, 403)
