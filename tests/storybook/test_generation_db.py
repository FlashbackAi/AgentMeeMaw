"""DB-backed tests for on-demand storybook generate / regenerate / edit.

settings=None makes the assembler fall back (no live LLM, so no emitted tags);
artifact_queue=None skips the SQS push. We assert the floor, the minted row +
no-cover context, accumulation of multiple books, scope filtering, and the
regenerate / edit row updates.
"""

from __future__ import annotations

import pytest

from flashback.storybook.generation import (
    StorybookTooThin,
    edit_storybook,
    generate_storybook,
    regenerate_storybook,
)
from flashback.storybook.repository import STORYBOOK_MIN_MOMENTS


async def _make_person(pool, name: str = "Dad") -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name, relationship) "
                    "VALUES (%s, %s) RETURNING id::text",
                    (name, "father"),
                )
                return (await cur.fetchone())[0]


async def _add_qualifying_moments(
    pool, person_id: str, n: int, *, life_period: str | None = None
) -> None:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                for i in range(n):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details, life_period_estimate) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (person_id, f"m{i}", "n", "the smell of rain", life_period),
                    )


async def _count_storybooks(pool, person_id: str) -> int:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM storybooks WHERE person_id = %s", (person_id,)
            )
            return int((await cur.fetchone())[0])


async def _fetch_storybook(pool, storybook_id: str):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, (latest_generation_context ? 'pages'), "
                "(latest_generation_context ? 'cover'), moments_count, tags "
                "FROM storybooks WHERE id = %s",
                (storybook_id,),
            )
            return await cur.fetchone()


async def test_below_floor_raises(async_pool) -> None:
    person_id = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, person_id, STORYBOOK_MIN_MOMENTS - 1)

    with pytest.raises(StorybookTooThin):
        await generate_storybook(
            db_pool=async_pool,
            settings=None,
            artifact_queue=None,
            person_id=person_id,
        )
    assert await _count_storybooks(async_pool, person_id) == 0


async def test_generate_mints_no_cover_book(async_pool) -> None:
    person_id = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, person_id, 5)

    result = await generate_storybook(
        db_pool=async_pool, settings=None, artifact_queue=None, person_id=person_id
    )

    assert result.storybook_id
    assert result.moments_count == 5
    assert result.scene_count > 0
    assert result.tags == []  # settings=None -> assembler emits no tags

    status_val, has_pages, has_cover, moments_count, tags = await _fetch_storybook(
        async_pool, result.storybook_id
    )
    assert status_val == "generating"
    assert has_pages is True
    assert has_cover is False  # standalone storybooks have no cover page
    assert moments_count == 5
    assert tags == []


async def test_multiple_storybooks_accumulate(async_pool) -> None:
    person_id = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, person_id, 5)

    await generate_storybook(
        db_pool=async_pool, settings=None, artifact_queue=None, person_id=person_id
    )
    await generate_storybook(
        db_pool=async_pool, settings=None, artifact_queue=None, person_id=person_id
    )

    # On-demand: each call mints a fresh book; nothing is superseded.
    assert await _count_storybooks(async_pool, person_id) == 2


async def test_scope_by_life_period_narrows_pool(async_pool) -> None:
    person_id = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, person_id, 4, life_period="childhood")
    await _add_qualifying_moments(async_pool, person_id, 6, life_period="career")

    result = await generate_storybook(
        db_pool=async_pool,
        settings=None,
        artifact_queue=None,
        person_id=person_id,
        life_period="career",
    )
    assert result.moments_count == 6


async def test_regenerate_overrides_tags_and_keeps_book(async_pool) -> None:
    person_id = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, person_id, 5)
    gen = await generate_storybook(
        db_pool=async_pool, settings=None, artifact_queue=None, person_id=person_id
    )

    regen = await regenerate_storybook(
        db_pool=async_pool,
        settings=None,
        artifact_queue=None,
        storybook_id=gen.storybook_id,
        person_id=person_id,
        tags=["happiness", "not_a_real_tag"],
    )

    assert regen.storybook_id == gen.storybook_id
    assert regen.tags == ["happiness"]  # unknown slug dropped
    # No new row; the same book is re-rendered.
    assert await _count_storybooks(async_pool, person_id) == 1
    _status, has_pages, has_cover, _count, tags = await _fetch_storybook(
        async_pool, gen.storybook_id
    )
    assert has_pages is True and has_cover is False
    assert tags == ["happiness"]


async def test_regenerate_unknown_storybook_raises(async_pool) -> None:
    person_id = await _make_person(async_pool)
    from flashback.storybook.generation import StorybookNotFound

    with pytest.raises(StorybookNotFound):
        await regenerate_storybook(
            db_pool=async_pool,
            settings=None,
            artifact_queue=None,
            storybook_id="00000000-0000-0000-0000-000000000000",
            person_id=person_id,
        )


async def test_edit_reshapes_existing_book(async_pool) -> None:
    person_id = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, person_id, 5)
    gen = await generate_storybook(
        db_pool=async_pool, settings=None, artifact_queue=None, person_id=person_id
    )

    edited = await edit_storybook(
        db_pool=async_pool,
        settings=None,
        artifact_queue=None,
        storybook_id=gen.storybook_id,
        person_id=person_id,
        instructions="Make it warmer.",
        prior_instructions=[],
        tags=["warmth"],
    )

    assert edited.storybook_id == gen.storybook_id
    assert edited.tags == ["warmth"]  # forced register
    assert await _count_storybooks(async_pool, person_id) == 1
