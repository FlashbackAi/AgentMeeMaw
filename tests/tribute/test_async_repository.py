"""DB tests for the async tribute repository surfaces."""

from __future__ import annotations

from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.repository import (
    ensure_open_tribute_async,
    ensure_standalone_tribute_async,
    set_message_async,
)
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)


async def _make_person(pool) -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Dad') RETURNING id::text"
                )
                return (await cur.fetchone())[0]


async def _seed_theme(pool, person_id: str) -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                return await ensure_tribute_theme_async(
                    cur,
                    person_id=person_id,
                    slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION,
                )


async def test_ensure_open_tribute_is_idempotent(async_pool) -> None:
    person_id = await _make_person(async_pool)
    theme_id = await _seed_theme(async_pool, person_id)
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                first = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                second = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
    assert first == second


async def _campaign_id(pool, slug: str) -> str:
    """Get (or create-as-published) a campaign row id for tests."""
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id::text FROM tribute_campaigns "
                    "WHERE slug = %s AND status = 'active'",
                    (slug,),
                )
                row = await cur.fetchone()
                if row:
                    return row[0]
                await cur.execute(
                    "INSERT INTO tribute_campaigns "
                    "(slug, display_name, state) "
                    "VALUES (%s, %s, 'published') RETURNING id::text",
                    (slug, slug.replace("_", " ").title()),
                )
                return (await cur.fetchone())[0]


async def test_campaign_entry_never_reopens_a_completed_tribute(
    async_pool,
) -> None:
    """Prod 2026-07-16: the FD video seemed to vanish when a new campaign
    generated — each campaign entry must get its OWN tribute row; the
    completed one (and its video_url) survives untouched."""
    person_id = await _make_person(async_pool)
    theme_id = await _seed_theme(async_pool, person_id)
    fd = await _campaign_id(async_pool, "fathers_day_2026")
    friend = await _campaign_id(async_pool, "friend_day_test")

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                fd_tribute = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=fd,
                )
                await cur.execute(
                    "UPDATE tributes SET status = 'complete', "
                    "video_url = 'https://s3/fd.mp4' WHERE id = %s",
                    (fd_tribute,),
                )
                friend_tribute = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=friend,
                )
                assert friend_tribute != fd_tribute
                # the FD row + video are untouched
                await cur.execute(
                    "SELECT status, video_url, campaign_id::text "
                    "FROM tributes WHERE id = %s",
                    (fd_tribute,),
                )
                status, video, camp = await cur.fetchone()
                assert (status, video, camp) == (
                    "complete", "https://s3/fd.mp4", fd,
                )
                # the new row is stamped with ITS campaign at insert
                await cur.execute(
                    "SELECT campaign_id::text FROM tributes WHERE id = %s",
                    (friend_tribute,),
                )
                assert (await cur.fetchone())[0] == friend


async def test_open_tribute_of_another_campaign_is_not_hijacked(
    async_pool,
) -> None:
    person_id = await _make_person(async_pool)
    theme_id = await _seed_theme(async_pool, person_id)
    a = await _campaign_id(async_pool, "campaign_a_test")
    b = await _campaign_id(async_pool, "campaign_b_test")

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                open_a = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id, campaign_id=a
                )
                open_b = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id, campaign_id=b
                )
                assert open_a != open_b
                # re-entry under each campaign is still idempotent
                assert open_a == await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id, campaign_id=a
                )
                assert open_b == await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id, campaign_id=b
                )


async def test_campaign_entry_does_not_adopt_a_neutral_draft(
    async_pool,
) -> None:
    """A campaign entry gets its OWN row; it never adopts the neutral draft.

    It used to (pre-0039 shape: adopt rather than spawn a duplicate). Once every
    legacy owned a standalone keepsake row -- 0048's two-meter model plus the
    2026-07-22 backfill -- that adoption started converting keepsakes into
    campaign rows, which retroactively added a message slot they had never been
    asked to fill. Prod 2026-07-28: finished videos reading 65% + not-ready.
    """
    person_id = await _make_person(async_pool)
    theme_id = await _seed_theme(async_pool, person_id)
    camp = await _campaign_id(async_pool, "adopting_campaign_test")

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                neutral = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                under_campaign = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=camp,
                )
                assert under_campaign != neutral
                # The neutral row keeps its identity (and so its meter).
                await cur.execute(
                    "SELECT campaign_id FROM tributes WHERE id = %s", (neutral,)
                )
                assert (await cur.fetchone())[0] is None


async def test_keepsake_is_healed_when_only_a_campaign_row_exists(
    async_pool,
) -> None:
    """A legacy whose keepsake row is gone gets one back on tribute entry.

    Prod 2026-07-28: 14 legacies showed a campaign card and no keepsake card.
    The pre-af3ec20 lookup had adopted each keepsake and stamped a campaign_id
    onto it, and nothing ever re-created one -- insert_person only runs at
    creation, so the meter stayed absent until a hand-run backfill. The heal in
    apply_theme_unlock's tribute branch is this call.
    """
    person_id = await _make_person(async_pool)
    theme_id = await _seed_theme(async_pool, person_id)
    camp = await _campaign_id(async_pool, "healing_campaign_test")

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                # the stranded shape: a campaign row is the ONLY row
                campaign_row = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=camp,
                )
                healed = await ensure_standalone_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                assert healed != campaign_row
                await cur.execute(
                    "SELECT campaign_id FROM tributes WHERE id = %s", (healed,)
                )
                assert (await cur.fetchone())[0] is None
                # re-entry heals nothing further -- one keepsake, not a fork
                assert healed == await ensure_standalone_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                await cur.execute(
                    "SELECT count(*) FROM tributes WHERE person_id = %s "
                    "AND campaign_id IS NULL AND status <> 'superseded'",
                    (person_id,),
                )
                assert (await cur.fetchone())[0] == 1


async def test_heal_reuses_a_finished_keepsake_instead_of_forking_one(
    async_pool,
) -> None:
    """A completed keepsake still counts as present.

    Otherwise the heal would deal a second keepsake card to every legacy that
    already rendered one -- prod carries six such legacies from the pre-two-
    meter era, each finished row holding its own video.
    """
    person_id = await _make_person(async_pool)
    theme_id = await _seed_theme(async_pool, person_id)

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                keepsake = await ensure_standalone_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                await cur.execute(
                    "UPDATE tributes SET status = 'complete', "
                    "video_url = 'https://s3/keepsake.mp4' WHERE id = %s",
                    (keepsake,),
                )
                assert keepsake == await ensure_standalone_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )


async def test_tribute_status_view_exposes_campaign(async_pool) -> None:
    person_id = await _make_person(async_pool)
    theme_id = await _seed_theme(async_pool, person_id)
    camp = await _campaign_id(async_pool, "view_campaign_test")

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                tid = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=camp,
                )
                await cur.execute(
                    "SELECT campaign_id::text, campaign_slug, "
                    "campaign_display_name FROM tribute_status WHERE id = %s",
                    (tid,),
                )
                cid, slug, display = await cur.fetchone()
                assert cid == camp
                assert slug == "view_campaign_test"
                assert display == "View Campaign Test"


async def test_set_message_async_persists(async_pool) -> None:
    person_id = await _make_person(async_pool)
    theme_id = await _seed_theme(async_pool, person_id)
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                tribute_id = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                await set_message_async(
                    cur,
                    tribute_id=tribute_id,
                    message_text="Thanks, Dad.",
                    source_turns=[{"text": "raw"}],
                )
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT message_text FROM tributes WHERE id = %s", (tribute_id,)
            )
            (msg,) = await cur.fetchone()
    assert msg == "Thanks, Dad."
