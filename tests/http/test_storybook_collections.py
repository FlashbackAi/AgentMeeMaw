"""GET /storybook-collections — the fixed chooser registry."""

from __future__ import annotations

_HEADERS = {"X-Service-Token": "test-token"}


async def test_lists_six_collections(client) -> None:
    r = await client.get("/storybook-collections", headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 6
    assert {c["slug"] for c in body} == {
        "childhood",
        "interesting",
        "nostalgia",
        "festivals",
        "adventurous",
        "wisdom",
    }
    assert all(c["page_count"] == 7 for c in body)
    layouts = {c["slug"]: c["layout"] for c in body}
    assert layouts["wisdom"] == "chapter"
    assert layouts["childhood"] == "grid"


async def test_requires_service_token(client) -> None:
    r = await client.get("/storybook-collections")
    assert r.status_code in (401, 403)
