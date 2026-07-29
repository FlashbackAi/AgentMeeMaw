"""POST /tributes/{id}/regenerate — re-render from the SAME stored inputs.

Regenerate reuses the prior tribute_video context verbatim and only overlays
fresh presigned URLs + a new composed_at. The DB-touching cases use the shared
client_with_db + async_db_pool fixtures (skip without TEST_DATABASE_URL); the
model case is pure and always runs.
"""

from __future__ import annotations

import json
from uuid import uuid4

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
                # >= 12 qualifying moments (the 0051 story floor), message
                # present.
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
    assert body["scene_count"] == 12

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
    snapshot's pinned campaign.

    It must NOT stamp the row (changed 2026-07-28). The snapshot's campaign is
    resolved with a featured-campaign fallback, so "snapshot pins a campaign"
    is not evidence of a campaign flow -- and campaign_id NULL has meant
    STANDALONE KEEPSAKE since 0048. Stamping it converted keepsakes into
    campaign rows, which retroactively added a message slot they were never
    asked to fill: three prod tributes rendered fine and then read 65% +
    not-ready with a finished video sitting on them."""
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
    # The config follows the snapshot's pinned campaign (the 07-16 fix)...
    assert snapshot_campaign == campaign_id
    # ...while the ROW stays standalone: a re-render never converts a keepsake.
    assert stamped is None


async def test_regenerate_follows_campaign_supersession(
    client_with_db, async_db_pool
) -> None:
    """Prod 2026-07-16 (evening): a theme swap on the campaign never
    reached regenerated videos — the completed tribute's stamped campaign
    row id resolved to the OLD superseded version. Re-resolution must
    freshen the stamp to the slug's current published row."""
    person_id, tribute_id = await _seed_ready(async_db_pool)
    await _generate(client_with_db, person_id, tribute_id)

    from flashback.tribute import config_repository as repo

    async with async_db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id::text FROM tribute_campaigns "
                    "WHERE slug = 'fathers_day_2026' AND status = 'active'"
                )
                (old_id,) = await cur.fetchone()
                # the completed-tribute shape: stamped with the then-active row
                await cur.execute(
                    "UPDATE tributes SET campaign_id = %s WHERE id = %s",
                    (old_id, tribute_id),
                )
                # CRM edit supersedes -> new active version
                new_id = await repo.supersede_edit(
                    cur, "tribute_campaigns", old_id,
                    {"display_name": "A Letter to Dad (fresh)"},
                    updated_by="t",
                )
                # completed tributes are deliberately NOT repointed; force
                # the stale-stamp state regenerate must recover from
                await cur.execute(
                    "UPDATE tributes SET campaign_id = %s, "
                    "status = 'complete' WHERE id = %s",
                    (old_id, tribute_id),
                )

    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/regenerate",
        json={
            "person_id": person_id,
            "video_put_url": "https://s3.example/put/video?sig=3",
            "pdf_put_url": "https://s3.example/put/pdf?sig=3",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT latest_generation_context -> 'tribute_video' ->> "
                "'campaign_id' FROM tributes WHERE id = %s", (tribute_id,))
            (snapshot_campaign,) = await cur.fetchone()
            # restore the seeded campaign for other tests
            await cur.execute(
                "UPDATE tribute_campaigns SET status = 'superseded' "
                "WHERE id = %s", (new_id,))
            await cur.execute(
                "UPDATE tribute_campaigns SET status = 'active' "
                "WHERE id = %s", (old_id,))
            await conn.commit()
    assert snapshot_campaign == new_id  # fresh version, not the stale stamp


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


# --- Thin-slice repair (prod 2026-07-28) ------------------------------------
#
# Snapshots composed before 3fb262f carry only the theme-tagged moments. A
# legacy with 18 qualifying memories had a ONE-page video because /generate
# fetched the single tagged moment, and every regenerate reused that list
# verbatim -- so the book could never heal (and /generate 409s once a video
# exists). A re-render now re-reads the live pool when the stored slice is
# below the story floor.


async def test_regenerate_widens_a_thin_stored_slice(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_ready(async_db_pool)
    await _generate(client_with_db, person_id, tribute_id)

    # Simulate the pre-3fb262f snapshot: one candidate stored, pool has 12.
    async with async_db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE tributes SET latest_generation_context = jsonb_set("
                    "latest_generation_context, '{tribute_video,candidates}', "
                    "jsonb_build_array(latest_generation_context "
                    "-> 'tribute_video' -> 'candidates' -> 0)) WHERE id = %s",
                    (tribute_id,),
                )
                await cur.execute(
                    "SELECT jsonb_array_length(latest_generation_context "
                    "-> 'tribute_video' -> 'candidates') FROM tributes WHERE id = %s",
                    (tribute_id,),
                )
                assert (await cur.fetchone())[0] == 1

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
    assert resp.json()["scene_count"] == 12

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT latest_generation_context -> 'tribute_video' "
                "FROM tributes WHERE id = %s", (tribute_id,))
            ctx = (await cur.fetchone())[0]
    assert len(ctx["candidates"]) == 12
    assert ctx["n_pages"] == 12


async def test_regenerate_keeps_a_healthy_slice_verbatim(
    client_with_db, async_db_pool
) -> None:
    """At or above the floor the stored slice is untouched -- including its
    telling order, which a re-fetch would reverse."""
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
            "video_put_url": "https://s3.example/put/video?sig=2",
            "pdf_put_url": "https://s3.example/put/pdf?sig=2",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT latest_generation_context -> 'tribute_video' "
                "FROM tributes WHERE id = %s", (tribute_id,))
            ctx = (await cur.fetchone())[0]
    assert ctx["candidates"] == prior["candidates"]


# --- A keepsake stays a keepsake (prod 2026-07-28) --------------------------


async def test_campaign_entry_does_not_adopt_the_keepsake_row(
    async_db_pool,
) -> None:
    """The conversion path behind the 65%-with-a-finished-video rows.

    Every legacy owns a standalone keepsake row (0048 + the 2026-07-22
    backfill). A campaign entry used to latch onto it (the lookup matched
    `campaign_id IS NULL`) and stamp it, which retroactively added a message
    slot the keepsake had never been asked to fill. It must get its own row.
    """
    from flashback.tribute.repository import (
        ensure_standalone_tribute_async,
        fetch_open_tribute_id_async,
    )

    async with async_db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Friend') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                theme_id = await ensure_tribute_theme_async(
                    cur, person_id=person_id, slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION)
                keepsake_id = await ensure_standalone_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id)
                await cur.execute(
                    "SELECT id::text FROM tribute_campaigns "
                    "WHERE slug = 'fathers_day_2026' AND status = 'active'")
                (campaign_id,) = await cur.fetchone()

                # A campaign-scoped lookup must not see the keepsake.
                adopted = await fetch_open_tribute_id_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=campaign_id)
                assert adopted is None
                adopted_by_slug = await fetch_open_tribute_id_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_slug="fathers_day_2026")
                assert adopted_by_slug is None

                # The campaign entry gets its own row, stamped at insert.
                campaign_tribute = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=campaign_id)
                assert campaign_tribute != keepsake_id
                await cur.execute(
                    "SELECT campaign_id::text FROM tributes WHERE id = %s",
                    (campaign_tribute,))
                assert (await cur.fetchone())[0] == campaign_id
                # ...and the keepsake is untouched: still standalone.
                await cur.execute(
                    "SELECT campaign_id FROM tributes WHERE id = %s",
                    (keepsake_id,))
                assert (await cur.fetchone())[0] is None


async def test_campaign_entry_reuses_the_row_across_crm_versions(
    async_db_pool,
) -> None:
    """A CRM republish (new id, same slug) must reuse the occasion's tribute.

    Matching the exact campaign id forked a second row per republish -- ten
    versions of the friendship-day slug by 2026-07-28 -- so the gallery showed
    two cards for one occasion and one of them was stranded mid-meter.
    """
    async with async_db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Friend') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                theme_id = await ensure_tribute_theme_async(
                    cur, person_id=person_id, slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION)
                # Own slug: the shared test DB's seeded campaigns are read by
                # other tests, so never supersede one of those here.
                slug = f"fork_test_{uuid4().hex[:12]}"
                await cur.execute(
                    "INSERT INTO tribute_campaigns (slug, display_name, state, "
                    "version, status) VALUES (%s, 'v1', 'published', 1, 'active') "
                    "RETURNING id::text", (slug,))
                (v1_id,) = await cur.fetchone()
                first = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=v1_id)

                # Republish: same slug, new row id, version+1 (house rules).
                await cur.execute(
                    "UPDATE tribute_campaigns SET status = 'superseded' "
                    "WHERE id = %s", (v1_id,))
                await cur.execute(
                    "INSERT INTO tribute_campaigns (slug, display_name, state, "
                    "version, status) VALUES (%s, 'v2', 'published', 2, 'active') "
                    "RETURNING id::text", (slug,))
                (v2_id,) = await cur.fetchone()

                again = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=v2_id)
    assert again == first
