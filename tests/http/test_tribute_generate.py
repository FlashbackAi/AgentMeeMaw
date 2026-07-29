"""POST /tributes/{id}/generate — gating + happy path against the test DB.

Uses the shared client_with_db + async_db_pool fixtures (tests/http/conftest).
No LLM call is made here anymore: Book assembly moved to the render worker, so
the route only writes the assembly INPUTS + presigned URLs into the keyed
latest_generation_context and flips status -- which is what we assert.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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
                    for i in range(12):
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
    # worker's job now). The 12 seeded moments are the candidates.
    assert "book" not in ctx
    assert len(ctx["candidates"]) == 12
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


# --- Duplicate-render guard -------------------------------------------------
#
# /generate is the first mint and is NOT retry-safe: the Node FE is meant to
# gate the button, and when it didn't (prod 2026-07-28), a second click bought
# a second paid render and flipped the row back to 'generating' while the
# finished video_url was still on it -- which is what made the card oscillate.
# The guard lives here, at the boundary that spends the money.


def _body(person_id: str, sig: str) -> dict:
    return {
        "person_id": person_id,
        "artifact_kind": "tribute_video",
        "video_put_url": f"https://s3.example/put/video?sig={sig}",
        "pdf_put_url": f"https://s3.example/put/pdf?sig={sig}",
    }


async def _render_state(pool, tribute_id: str) -> tuple[str, str]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, latest_generation_context -> 'tribute_video' "
                "->> 'composed_at' FROM tributes WHERE id = %s",
                (tribute_id,),
            )
            return await cur.fetchone()


async def test_video_409_while_a_render_is_in_flight(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id, _ = await _seed(async_db_pool, ready=True)
    first = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json=_body(person_id, "1"),
        headers=_HEADERS,
    )
    assert first.status_code == 200, first.text
    _, composed_at = await _render_state(async_db_pool, tribute_id)

    second = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json=_body(person_id, "2"),
        headers=_HEADERS,
    )
    assert second.status_code == 409, second.text
    assert "already in progress" in second.json()["detail"]
    # The in-flight render is untouched: same composed_at (so the running job
    # does not go stale) and the URLs it was given are still the stored ones.
    status_val, still = await _render_state(async_db_pool, tribute_id)
    assert (status_val, still) == ("generating", composed_at)


async def test_video_409_when_already_rendered(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id, _ = await _seed(async_db_pool, ready=True)
    first = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json=_body(person_id, "1"),
        headers=_HEADERS,
    )
    assert first.status_code == 200, first.text
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE tributes SET status = 'complete', "
                "video_url = 's3://bucket/video.mp4' WHERE id = %s",
                (tribute_id,),
            )

    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json=_body(person_id, "2"),
        headers=_HEADERS,
    )
    assert resp.status_code == 409, resp.text
    assert "/regenerate" in resp.json()["detail"]
    status_val, _ = await _render_state(async_db_pool, tribute_id)
    assert status_val == "complete"


async def test_video_retry_allowed_after_a_dead_render(
    client_with_db, async_db_pool
) -> None:
    """A render stuck in 'generating' past the grace window is retryable --
    otherwise a dead worker wedges the tribute shut forever."""
    person_id, tribute_id, _ = await _seed(async_db_pool, ready=True)
    first = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json=_body(person_id, "1"),
        headers=_HEADERS,
    )
    assert first.status_code == 200, first.text
    _, composed_at = await _render_state(async_db_pool, tribute_id)

    # Backdate in the SAME format the route writes (datetime.isoformat), so the
    # guard's parse path is what's under test -- an unparseable stamp also lets
    # the retry through, which would make this pass for the wrong reason.
    stale_stamp = (
        datetime.now(timezone.utc) - timedelta(minutes=90)
    ).isoformat()
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE tributes SET latest_generation_context = jsonb_set("
                "latest_generation_context, '{tribute_video,composed_at}', "
                "to_jsonb(%s::text)) WHERE id = %s",
                (stale_stamp, tribute_id),
            )
    _, backdated = await _render_state(async_db_pool, tribute_id)
    assert backdated == stale_stamp

    retry = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json=_body(person_id, "2"),
        headers=_HEADERS,
    )
    assert retry.status_code == 200, retry.text
    status_val, fresh = await _render_state(async_db_pool, tribute_id)
    assert status_val == "generating"
    assert fresh not in (None, composed_at)
