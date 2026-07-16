"""POST /tributes/{id}/regenerate — re-render from the SAME stored inputs.

Regenerate reuses the prior tribute_video context verbatim and only overlays
fresh presigned URLs + a new composed_at. The DB-touching cases use the shared
client_with_db + async_db_pool fixtures (skip without TEST_DATABASE_URL); the
model case is pure and always runs.
"""

from __future__ import annotations

import json

from flashback.http.models import TributeRegenerateRequest
from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.repository import ensure_open_tribute_async, set_message_async
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)

_HEADERS = {"X-Service-Token": "test-token"}


def test_request_forbids_extra_fields() -> None:
    req = TributeRegenerateRequest(
        person_id="00000000-0000-0000-0000-000000000001",
        video_put_url="https://s3.example/put/video?sig=2",
        pdf_put_url="https://s3.example/put/pdf?sig=2",
    )
    assert req.poster_put_url is None
    assert req.prime_photo_get_url is None


async def _seed_ready(pool) -> tuple[str, str]:
    """A ready tribute (3 moments + message) so /generate stores a context."""
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Dad') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                theme_id = await ensure_tribute_theme_async(
                    cur, person_id=person_id, slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION)
                tribute_id = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id)
                # Meter needs all four components: appearance = region +
                # birth_era + attire-or-features, signature = a trait,
                # >= 3 qualifying moments, message present.
                gt = json.dumps({
                    "region": {"value": "South India"},
                    "birth_era": {"value": "1950s"},
                    "attire": {"value": "crisp white shirt"},
                })
                await cur.execute(
                    "UPDATE persons SET ground_truth = %s WHERE id = %s",
                    (gt, person_id))
                for i in range(3):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details, time_anchor) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (person_id, f"m{i}", "n", "the smell of rain",
                         json.dumps({"year": 2009})))
                await cur.execute(
                    "INSERT INTO traits (person_id, name, description) "
                    "VALUES (%s, 'steady', 'Showed up every day.')",
                    (person_id,))
                await set_message_async(
                    cur, tribute_id=tribute_id, message_text="Thank you, Dad.")
    return person_id, tribute_id


async def _generate(client, person_id, tribute_id) -> None:
    resp = await client.post(
        f"/tributes/{tribute_id}/generate",
        json={
            "person_id": person_id,
            "artifact_kind": "tribute_video",
            "video_put_url": "https://s3.example/put/video?sig=1",
            "pdf_put_url": "https://s3.example/put/pdf?sig=1",
            "prime_photo_get_url": "https://s3.example/get/photo?sig=1",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text


async def test_regenerate_reuses_inputs_with_fresh_urls(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)
    await _generate(client_with_db, person_id, tribute_id)

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT latest_generation_context -> 'tribute_video' "
                "FROM tributes WHERE id = %s", (tribute_id,))
            prior = (await cur.fetchone())[0]

    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/regenerate",
        json={
            "person_id": person_id,
            "video_put_url": "https://s3.example/put/video?sig=FRESH",
            "pdf_put_url": "https://s3.example/put/pdf?sig=FRESH",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["artifact_kind"] == "tribute_video"
    assert body["scene_count"] == 3

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, latest_generation_context -> 'tribute_video' "
                "FROM tributes WHERE id = %s", (tribute_id,))
            status_val, ctx = await cur.fetchone()
    assert status_val == "generating"
    # Fresh URLs overlaid; omitted prime photo cleared (its old URL expired).
    assert ctx["video_put_url"].endswith("sig=FRESH")
    assert ctx["pdf_put_url"].endswith("sig=FRESH")
    assert ctx["prime_photo_get_url"] == ""
    # Same inputs reused verbatim.
    assert ctx["candidates"] == prior["candidates"]
    assert ctx["message_text"] == prior["message_text"]
    # New composition supersedes the prior one.
    assert ctx["composed_at"] != prior["composed_at"]


async def test_regenerate_keeps_the_snapshot_campaign(
    client_with_db, async_db_pool
) -> None:
    """Prod 2026-07-16: regenerating an UNSTAMPED tribute reverted to the
    neutral (Father's Day) config. The re-resolution must follow the prior
    snapshot's pinned campaign and stamp the row."""
    person_id, tribute_id = await _seed_ready(async_db_pool)
    await _generate(client_with_db, person_id, tribute_id)

    # Simulate the prod shape: snapshot pins a campaign, row unstamped.
    async with async_db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id::text FROM tribute_campaigns "
                    "WHERE slug = 'fathers_day_2026' AND status = 'active'"
                )
                (campaign_id,) = await cur.fetchone()
                await cur.execute(
                    "UPDATE tributes SET campaign_id = NULL, "
                    "latest_generation_context = jsonb_set("
                    "latest_generation_context, "
                    "'{tribute_video,campaign_id}', to_jsonb(%s::text)) "
                    "WHERE id = %s",
                    (campaign_id, tribute_id),
                )

    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/regenerate",
        json={
            "person_id": person_id,
            "video_put_url": "https://s3.example/put/video?sig=2",
            "pdf_put_url": "https://s3.example/put/pdf?sig=2",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT campaign_id::text, "
                "latest_generation_context -> 'tribute_video' ->> 'campaign_id' "
                "FROM tributes WHERE id = %s", (tribute_id,))
            stamped, snapshot_campaign = await cur.fetchone()
    # snapshot still pins the campaign; the row got backstop-stamped
    assert snapshot_campaign == campaign_id
    assert stamped == campaign_id


async def test_regenerate_404_without_prior_context(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)  # never generated
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/regenerate",
        json={
            "person_id": person_id,
            "video_put_url": "https://s3.example/put/video?sig=1",
            "pdf_put_url": "https://s3.example/put/pdf?sig=1",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 404, resp.text


async def test_regenerate_400_without_urls(client_with_db, async_db_pool) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)
    await _generate(client_with_db, person_id, tribute_id)
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/regenerate",
        json={"person_id": person_id},
        headers=_HEADERS,
    )
    assert resp.status_code == 400, resp.text


async def test_regenerate_404_on_person_mismatch(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)
    await _generate(client_with_db, person_id, tribute_id)
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/regenerate",
        json={
            "person_id": "00000000-0000-0000-0000-000000000099",
            "video_put_url": "https://s3.example/put/video?sig=1",
            "pdf_put_url": "https://s3.example/put/pdf?sig=1",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 404, resp.text
