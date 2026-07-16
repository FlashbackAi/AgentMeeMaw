"""DB tests for the async tribute repository surfaces."""

from __future__ import annotations

from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.repository import (
    ensure_open_tribute_async,
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


async def test_campaign_entry_adopts_an_unstamped_open_draft(
    async_pool,
) -> None:
    """Pre-0039 shape: an open neutral draft is adopted by the first
    campaign entry instead of spawning a duplicate."""
    person_id = await _make_person(async_pool)
    theme_id = await _seed_theme(async_pool, person_id)
    camp = await _campaign_id(async_pool, "adopting_campaign_test")

    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                neutral = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                adopted = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id,
                    campaign_id=camp,
                )
                assert adopted == neutral


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
