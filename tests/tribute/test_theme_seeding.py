"""The on-demand tribute theme seeds as kind='tribute', locked, idempotent."""

from __future__ import annotations

from flashback.themes.repository import ensure_tribute_theme_async
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


async def test_ensure_tribute_theme_idempotent_and_locked(async_pool) -> None:
    person_id = await _make_person(async_pool)
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                first = await ensure_tribute_theme_async(
                    cur,
                    person_id=person_id,
                    slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION,
                )
                second = await ensure_tribute_theme_async(
                    cur,
                    person_id=person_id,
                    slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION,
                )
                await cur.execute(
                    "SELECT kind, state FROM themes WHERE id = %s", (first,)
                )
                kind, state = await cur.fetchone()
    assert first == second
    assert kind == "tribute"
    assert state == "locked"
