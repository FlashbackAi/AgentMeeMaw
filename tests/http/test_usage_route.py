import pytest


@pytest.mark.asyncio
async def test_post_usage_events_inserts_node_row(client_with_db, async_db_pool):
    resp = await client_with_db.post("/usage/events", json={
        "feature": "artifact_image", "provider": "gemini", "model": "img-1",
        "units": 1, "unit_type": "images", "cost_usd": 0.04,
    })
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    assert new_id

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT service, feature, unit_type, cost_usd FROM usage_events "
                "WHERE id = %s", (new_id,))
            row = await cur.fetchone()
    assert row[0] == "node"           # forced server-side
    assert row[1] == "artifact_image"
    assert row[2] == "images"
    assert float(row[3]) == pytest.approx(0.04)


@pytest.mark.asyncio
async def test_post_usage_events_rejects_missing_required_field(client_with_db):
    resp = await client_with_db.post("/usage/events", json={
        "feature": "artifact_image", "provider": "gemini",  # missing model + cost_usd
    })
    assert resp.status_code == 422
