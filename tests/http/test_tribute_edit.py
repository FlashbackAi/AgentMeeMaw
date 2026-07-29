"""POST /tributes/{id}/edit and /edit-suggestions.

Edit re-renders from the stored inputs with cumulative free-text adjustments
(prior_instructions + instructions) stored as edit_instructions on the context.
The DB-touching cases use the shared client_with_db + async_db_pool fixtures
(skip/need TEST_DATABASE_URL); the model case is pure.
"""

from __future__ import annotations

import json

from flashback.http.models import TributeEditRequest
from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.repository import ensure_open_tribute_async, set_message_async
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)

_HEADERS = {"X-Service-Token": "test-token"}


def test_request_defaults() -> None:
    req = TributeEditRequest(
        person_id="00000000-0000-0000-0000-000000000001",
        instructions="warmer",
        video_put_url="https://s3.example/put/video?sig=1",
        pdf_put_url="https://s3.example/put/pdf?sig=1",
    )
    assert req.prior_instructions == []
    assert req.poster_put_url is None


async def _seed_ready(pool) -> tuple[str, str]:
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
                # The meter needs all four components at full weight:
                # appearance = region + birth_era/era_span + attire-or-features,
                # signature = any active trait, moments >= 12 qualifying
                # (the 0051 story floor), message present.
                gt = json.dumps({
                    "region": {"value": "South India"},
                    "birth_era": {"value": "1950s"},
                    "attire": {"value": "crisp white shirt"},
                })
                await cur.execute(
                    "UPDATE persons SET ground_truth = %s WHERE id = %s",
                    (gt, person_id))
                for i in range(12):
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
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text


async def _ctx(pool, tribute_id):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, latest_generation_context -> 'tribute_video' "
                "FROM tributes WHERE id = %s", (tribute_id,))
            return await cur.fetchone()


async def test_edit_stores_cumulative_instructions(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)
    await _generate(client_with_db, person_id, tribute_id)

    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/edit",
        json={
            "person_id": person_id,
            "prior_instructions": ["Make it warmer."],
            "instructions": "Lean on the fishing trips.",
            "video_put_url": "https://s3.example/put/video?sig=E",
            "pdf_put_url": "https://s3.example/put/pdf?sig=E",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    status_val, ctx = await _ctx(async_db_pool, tribute_id)
    assert status_val == "generating"
    assert ctx["edit_instructions"] == [
        "Make it warmer.", "Lean on the fishing trips."]
    assert ctx["video_put_url"].endswith("sig=E")
    assert len(ctx["candidates"]) == 12  # inputs reused


async def test_edit_400_without_instructions(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)
    await _generate(client_with_db, person_id, tribute_id)
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/edit",
        json={
            "person_id": person_id,
            "video_put_url": "https://s3.example/put/video?sig=1",
            "pdf_put_url": "https://s3.example/put/pdf?sig=1",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 400, resp.text


async def test_edit_400_without_urls(client_with_db, async_db_pool) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)
    await _generate(client_with_db, person_id, tribute_id)
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/edit",
        json={"person_id": person_id, "instructions": "warmer"},
        headers=_HEADERS,
    )
    assert resp.status_code == 400, resp.text


async def test_edit_404_without_prior_context(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)  # never generated
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/edit",
        json={
            "person_id": person_id,
            "instructions": "warmer",
            "video_put_url": "https://s3.example/put/video?sig=1",
            "pdf_put_url": "https://s3.example/put/pdf?sig=1",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 404, resp.text


async def test_edit_suggestions_404_without_prior_context(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/edit-suggestions",
        json={"person_id": person_id},
        headers=_HEADERS,
    )
    assert resp.status_code == 404, resp.text


async def test_edit_suggestions_returns_chips(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)
    await _generate(client_with_db, person_id, tribute_id)
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/edit-suggestions",
        json={"person_id": person_id},
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    chips = resp.json()["suggestions"]
    assert chips  # at least the fallback catalog
    assert all(c["label"] and c["instruction"] for c in chips)
