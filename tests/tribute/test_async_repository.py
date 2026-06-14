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
