"""GET /tribute-campaigns returns the list (no DB needed)."""

from __future__ import annotations


async def test_list_campaigns(client) -> None:
    resp = await client.get(
        "/tribute-campaigns", headers={"X-Service-Token": "test-token"}
    )
    assert resp.status_code == 200
    body = resp.json()
    slugs = {c["slug"] for c in body["campaigns"]}
    assert "default" in slugs
    assert "fathers_day_2026" in slugs
