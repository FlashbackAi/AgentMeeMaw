"""GET /tribute-campaigns is DB-backed (published rows + neutral first)."""

from __future__ import annotations


async def test_list_campaigns(client_with_db) -> None:
    resp = await client_with_db.get(
        "/tribute-campaigns", headers={"X-Service-Token": "test-token"}
    )
    assert resp.status_code == 200
    body = resp.json()
    slugs = [c["slug"] for c in body["campaigns"]]
    assert slugs[0] == "default"  # neutral always first
    assert "fathers_day_2026" in slugs
    fd = next(c for c in body["campaigns"] if c["slug"] == "fathers_day_2026")
    assert fd["featured"] is True
    assert fd["active_start"] == "2026-06-01"
    # The June window has passed; nothing is featured "today".
    assert fd["is_active"] is False
    assert body["active_featured_slug"] is None
