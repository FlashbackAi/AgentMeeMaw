"""POST /tributes/{id}/generate — gating + happy path against the test DB.

Uses the shared client_with_db + async_db_pool fixtures (tests/http/conftest).
No LLM call is made here anymore: Book assembly moved to the render worker, so
the route only writes the assembly INPUTS + presigned URLs into the keyed
latest_generation_context and flips status -- which is what we assert.
"""

from __future__ import annotations

import json

from flashback.http.models import TributeGenerateRequest
from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.repository import ensure_open_tribute_async, set_message_async
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)

_HEADERS = {"X-Service-Token": "test-token"}


def test_request_accepts_prime_photo_key() -> None:
    req = TributeGenerateRequest(
        person_id="00000000-0000-0000-0000-000000000001",
        artifact_kind="storybook",
        campaign="fathers_day_2026",
        prime_photo_s3_key="uploads/p/prime.jpg",
    )
    assert req.prime_photo_s3_key == "uploads/p/prime.jpg"


def test_request_prime_photo_defaults_none() -> None:
    req = TributeGenerateRequest(
        person_id="00000000-0000-0000-0000-000000000001",
    )
    assert req.prime_photo_s3_key is None
    # De-age by default (the common fallback is a current/profile photo).
    assert req.cover_photo_is_prime_years is False


def test_request_can_mark_photo_as_prime_years() -> None:
    req = TributeGenerateRequest(
        person_id="00000000-0000-0000-0000-000000000001",
        artifact_kind="storybook",
        prime_photo_s3_key="uploads/p/prime.jpg",
        cover_photo_is_prime_years=True,
    )
    assert req.cover_photo_is_prime_years is True


async def _seed(pool, *, ready: bool) -> tuple[str, str, str | None]:
    """Return (person_id, tribute_id, a_real_moment_id_or_None)."""
    moment_id: str | None = None
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Dad') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                theme_id = await ensure_tribute_theme_async(
                    cur,
                    person_id=person_id,
                    slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION,
                )
                tribute_id = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                if ready:
                    gt = json.dumps(
                        {
                            "region": {"value": "South India"},
                            "birth_era": {"value": "1950s"},
                            "attire": {"value": "white shirt"},
                        }
                    )
                    await cur.execute(
                        "UPDATE persons SET ground_truth = %s WHERE id = %s",
                        (gt, person_id),
                    )
                    for i in range(3):
                        await cur.execute(
                            "INSERT INTO moments (person_id, title, narrative, "
                            "sensory_details) VALUES (%s, %s, %s, %s) "
                            "RETURNING id::text",
                            (person_id, f"m{i}", "n", "the smell of rain"),
                        )
                        if moment_id is None:
                            moment_id = (await cur.fetchone())[0]
                    await cur.execute(
                        "INSERT INTO traits (person_id, name, status) "
                        "VALUES (%s, 'patient', 'active')",
                        (person_id,),
                    )
                    await set_message_async(
                        cur, tribute_id=tribute_id, message_text="Thank you, Dad."
                    )
    return person_id, tribute_id, moment_id


async def test_video_409_when_not_ready(client_with_db, async_db_pool) -> None:
    person_id, tribute_id, _ = await _seed(async_db_pool, ready=False)
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json={"person_id": person_id, "artifact_kind": "tribute_video"},
        headers=_HEADERS,
    )
    assert resp.status_code == 409


async def test_video_200_stores_context_inputs_and_enqueues(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id, _moment_id = await _seed(async_db_pool, ready=True)

    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json={
            "person_id": person_id,
            "artifact_kind": "tribute_video",
            "video_put_url": "https://s3.example/put/video?sig=1",
            "pdf_put_url": "https://s3.example/put/pdf?sig=1",
            "poster_put_url": "https://s3.example/put/poster?sig=1",
            "prime_photo_get_url": "https://s3.example/get/photo?sig=1",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["artifact_kind"] == "tribute_video"
    # Standalone tribute (no campaign): generate gates on `ready` (the story
    # floor), not percent==100 — soft slots (appearance/signature) leave the
    # bar below 100 while still unlocking (two-meter model, design 2026-07-22).
    assert body["ready"] is True

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, latest_generation_context -> 'tribute_video' "
                "FROM tributes WHERE id = %s",
                (tribute_id,),
            )
            status_val, ctx = await cur.fetchone()
    assert status_val == "generating"
    assert ctx is not None
    assert ctx["video_put_url"].startswith("https://s3.example/put/video")
    assert ctx["pdf_put_url"].startswith("https://s3.example/put/pdf")
    assert ctx["poster_put_url"].startswith("https://s3.example/put/poster")
    # The route stores assembly INPUTS, not a pre-built Book (assembly is the
    # worker's job now). The 3 seeded moments are the candidates.
    assert "book" not in ctx
    assert len(ctx["candidates"]) == 3
    assert ctx["message_text"] == "Thank you, Dad."
    assert ctx["composed_at"]


async def test_video_snapshot_carries_crm_config(
    client_with_db, async_db_pool
) -> None:
    """The snapshot pins config ids + composed directives (spec §6.4)."""
    person_id, tribute_id, _ = await _seed(async_db_pool, ready=True)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE persons SET relationship = 'best friend' WHERE id = %s",
                (person_id,),
            )

    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json={
            "person_id": person_id,
            "artifact_kind": "tribute_video",
            "campaign": "fathers_day_2026",
            "video_put_url": "https://s3.example/put/video?sig=1",
            "pdf_put_url": "https://s3.example/put/pdf?sig=1",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT t.latest_generation_context -> 'tribute_video', "
                "p.relationship_group FROM tributes t "
                "JOIN persons p ON p.id = t.person_id WHERE t.id = %s",
                (tribute_id,),
            )
            ctx, group = await cur.fetchone()
    assert group == "friend"  # resolved + cached during generate
    assert ctx["profile_id"] and ctx["campaign_id"]
    # friend register composed into the voice slots
    assert "partner-in-crime" in ctx["voice_block"]
    assert "{name}" in ctx["fallback_opener"]
    # FD campaign's deage override wins over the friend profile's False
    assert ctx["deage"] is True
    # visual theme pinned (classic_keepsake via profile default)
    assert ctx["style"]["audio_slug"] == "sentimental_piano"
    assert ctx["style"]["visual_theme_id"]


async def test_video_400_when_missing_presigned_urls(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id, _moment_id = await _seed(async_db_pool, ready=True)

    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json={"person_id": person_id, "artifact_kind": "tribute_video"},
        headers=_HEADERS,
    )
    assert resp.status_code == 400, resp.text
