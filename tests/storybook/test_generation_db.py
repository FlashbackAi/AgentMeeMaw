"""DB-backed tests for collection storybook generate / regenerate / edit.

The route layer is thin on the Python render pipeline: validate -> fetch the
qualifying pool -> write the render context on the row -> enqueue. We assert
the floor, the minted row + context shape, the regenerate reuse_script flag,
the edit instruction accumulation, and ownership checks. queue is a fake
producer (no SQS).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flashback.storybook.collections import PAGE_COUNT
from flashback.storybook.context import CONTEXT_KEY
from flashback.storybook.generation import (
    BadPageUrls,
    StorybookBadMomentIds,
    StorybookIdConflict,
    StorybookNotFound,
    StorybookSelectionOutOfBounds,
    StorybookTooThin,
    UnknownCollection,
    edit_storybook,
    generate_storybook,
    regenerate_storybook,
)
from flashback.storybook.repository import (
    STORYBOOK_COLLECTION_FLOOR,
    STORYBOOK_MIN_MOMENTS,
)


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


# Tag every inserted moment with all grid slugs so any grid collection's
# scoped pool sees them (design 2026-07-06). The wisdom lens counts the whole
# qualifying pool regardless of tags.
_ALL_GRID = ["childhood", "interesting", "nostalgia", "festivals", "adventurous"]


async def _add_qualifying_moments(
    pool, person_id: str, n: int, collections: list[str] | None = None
) -> None:
    tags = _ALL_GRID if collections is None else collections
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                for i in range(n):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details, storybook_collections) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (person_id, f"m{i}", "n", "the smell of rain", tags),
                    )


async def _fetch_row(pool, storybook_id: str):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, collection, latest_generation_context "
                "FROM storybooks WHERE id = %s",
                (storybook_id,),
            )
            return await cur.fetchone()


def _queue() -> AsyncMock:
    q = AsyncMock()
    q.push = AsyncMock(return_value="mid")
    return q


def _urls(**over):
    base = dict(
        pdf_put_url="https://s3.example/pdf?sig=1",
        cover_put_url="https://s3.example/cover?sig=1",
        page_put_urls=[
            f"https://s3.example/p{i}?sig=1" for i in range(PAGE_COUNT)
        ],
    )
    base.update(over)
    return base


async def test_below_floor_raises_keep_sharing(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_MIN_MOMENTS - 1)
    with pytest.raises(StorybookTooThin) as exc:
        await generate_storybook(
            db_pool=async_pool, queue=None, person_id=pid,
            collection="childhood", **_urls(),
        )
    assert "keep sharing" in str(exc.value)


async def test_unknown_collection_rejected(async_pool) -> None:
    pid = await _make_person(async_pool)
    with pytest.raises(UnknownCollection):
        await generate_storybook(
            db_pool=async_pool, queue=None, person_id=pid,
            collection="memoir", **_urls(),
        )


async def test_wrong_page_url_count_rejected(async_pool) -> None:
    pid = await _make_person(async_pool)
    with pytest.raises(BadPageUrls):
        await generate_storybook(
            db_pool=async_pool, queue=None, person_id=pid,
            collection="childhood",
            **_urls(page_put_urls=["https://s3.example/p0"]),
        )


async def test_generate_mints_row_with_context_then_enqueues(
    async_pool,
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_COLLECTION_FLOOR)
    q = _queue()
    result = await generate_storybook(
        db_pool=async_pool, queue=q, person_id=pid,
        collection="childhood",
        anchor_photo_get_url="https://s3.example/anchor?sig=1",
        **_urls(),
    )
    assert result.enqueued is True
    assert result.collection == "childhood"
    assert result.moments_count == STORYBOOK_COLLECTION_FLOOR
    row = await _fetch_row(async_pool, result.storybook_id)
    assert row[0] == "generating"
    assert row[1] == "childhood"
    ctx = row[2][CONTEXT_KEY]
    assert ctx["collection"] == "childhood"
    assert len(ctx["page_put_urls"]) == PAGE_COUNT
    assert ctx["anchor_photo_get_url"].startswith("https://s3.example/anchor")
    assert ctx["reuse_script"] is False
    assert ctx["composed_at"]
    # SQS payload is trigger-only and matches the stored composed_at
    push = q.push.call_args.kwargs
    assert push["storybook_id"] == result.storybook_id
    assert push["composed_at"] == ctx["composed_at"]


async def test_regenerate_sets_reuse_script(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_COLLECTION_FLOOR)
    made = await generate_storybook(
        db_pool=async_pool, queue=None, person_id=pid,
        collection="festivals", **_urls(),
    )
    result = await regenerate_storybook(
        db_pool=async_pool, queue=None,
        storybook_id=made.storybook_id, person_id=pid, **_urls(),
    )
    assert result.collection == "festivals"
    row = await _fetch_row(async_pool, made.storybook_id)
    ctx = row[2][CONTEXT_KEY]
    assert ctx["reuse_script"] is True
    assert ctx["edit_instructions"] == []


async def test_edit_accumulates_instructions(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_MIN_MOMENTS)
    made = await generate_storybook(
        db_pool=async_pool, queue=None, person_id=pid,
        collection="wisdom", **_urls(),
    )
    await edit_storybook(
        db_pool=async_pool, queue=None,
        storybook_id=made.storybook_id, person_id=pid,
        instructions="warmer",
        prior_instructions=["more about the pond"],
        **_urls(),
    )
    row = await _fetch_row(async_pool, made.storybook_id)
    ctx = row[2][CONTEXT_KEY]
    assert ctx["edit_instructions"] == ["more about the pond", "warmer"]
    assert ctx["reuse_script"] is False


async def test_caller_supplied_id_is_used(async_pool) -> None:
    """Node generates the id (its presigned S3 keys embed it) — the row must
    be created with exactly that id."""
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_COLLECTION_FLOOR)
    supplied = "11111111-2222-3333-4444-555555555555"
    result = await generate_storybook(
        db_pool=async_pool, queue=None, person_id=pid,
        collection="childhood", storybook_id=supplied, **_urls(),
    )
    assert result.storybook_id == supplied
    assert await _fetch_row(async_pool, supplied) is not None


async def test_duplicate_caller_supplied_id_conflicts(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_COLLECTION_FLOOR)
    supplied = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    await generate_storybook(
        db_pool=async_pool, queue=None, person_id=pid,
        collection="childhood", storybook_id=supplied, **_urls(),
    )
    with pytest.raises(StorybookIdConflict):
        await generate_storybook(
            db_pool=async_pool, queue=None, person_id=pid,
            collection="nostalgia", storybook_id=supplied, **_urls(),
        )


async def test_regenerate_unknown_storybook_raises(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_MIN_MOMENTS)
    with pytest.raises(StorybookNotFound):
        await regenerate_storybook(
            db_pool=async_pool, queue=None,
            storybook_id="00000000-0000-0000-0000-000000000009",
            person_id=pid, **_urls(),
        )


# --- user-confirmed selection (spec 2026-07-05) ------------------------------


async def _pool_ids(pool, person_id: str) -> list[str]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id::text FROM moments WHERE person_id = %s "
                "ORDER BY created_at",
                (person_id,),
            )
            return [r[0] for r in await cur.fetchall()]


async def test_confirmed_selection_filters_context_and_seeds_scene_ids(
    async_pool,
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 8)
    ids = await _pool_ids(async_pool, pid)
    chosen = ids[:5]
    result = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", moment_ids=chosen, **_urls(),
    )
    assert result.moments_count == 5
    row = await _fetch_row(async_pool, result.storybook_id)
    ctx = row[2][CONTEXT_KEY]
    assert ctx["user_curated"] is True
    assert [m["id"] for m in ctx["moments"]] == chosen
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT scene_moment_ids FROM storybooks WHERE id = %s",
                (result.storybook_id,),
            )
            scene_ids = (await cur.fetchone())[0]
    assert [str(s) for s in scene_ids] == chosen


async def test_selection_with_non_pool_id_rejected(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 6)
    ids = await _pool_ids(async_pool, pid)
    from uuid import uuid4
    with pytest.raises(StorybookBadMomentIds):
        await generate_storybook(
            db_pool=async_pool, queue=None, person_id=pid,
            collection="childhood",
            moment_ids=ids[:4] + [str(uuid4())], **_urls(),
        )


async def test_selection_bounds_enforced(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 8)
    ids = await _pool_ids(async_pool, pid)
    with pytest.raises(StorybookSelectionOutOfBounds):
        await generate_storybook(
            db_pool=async_pool, queue=None, person_id=pid,
            collection="childhood", moment_ids=ids[:4], **_urls(),
        )


async def test_thin_pool_relaxes_min_to_floor(async_pool) -> None:
    # Grid collections gate at 5, so the relaxation only applies to the wisdom
    # whole-pool lens (design 2026-07-06): a pool of 4 lets the user confirm 3.
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 4)
    ids = await _pool_ids(async_pool, pid)
    result = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="wisdom", moment_ids=ids[:3], **_urls(),
    )
    assert result.moments_count == 3


async def test_selection_deduped_before_bounds(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 8)
    ids = await _pool_ids(async_pool, pid)
    result = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood",
        moment_ids=ids[:5] + [ids[0]], **_urls(),
    )
    assert result.moments_count == 5


async def test_no_moment_ids_keeps_auto_path(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 6)
    result = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", **_urls(),
    )
    row = await _fetch_row(async_pool, result.storybook_id)
    ctx = row[2][CONTEXT_KEY]
    assert ctx["user_curated"] is False
    assert result.moments_count == 6


async def test_edit_preserves_user_selection(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 8)
    ids = await _pool_ids(async_pool, pid)
    chosen = ids[:5]
    minted = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", moment_ids=chosen, **_urls(),
    )
    result = await edit_storybook(
        db_pool=async_pool, queue=_queue(),
        storybook_id=minted.storybook_id, person_id=pid,
        instructions="warmer colours", prior_instructions=[],
        **_urls(),
    )
    assert result.moments_count == 5
    row = await _fetch_row(async_pool, minted.storybook_id)
    ctx = row[2][CONTEXT_KEY]
    assert ctx["user_curated"] is True
    assert [m["id"] for m in ctx["moments"]] == chosen


async def test_edit_falls_back_to_pool_when_selection_guts(
    async_pool,
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 8)
    ids = await _pool_ids(async_pool, pid)
    chosen = ids[:5]
    minted = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", moment_ids=chosen, **_urls(),
    )
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE moments SET status = 'superseded' "
                "WHERE id = ANY(%s::uuid[])",
                (chosen[:3],),
            )
    result = await edit_storybook(
        db_pool=async_pool, queue=_queue(),
        storybook_id=minted.storybook_id, person_id=pid,
        instructions="warmer colours", prior_instructions=[],
        **_urls(),
    )
    row = await _fetch_row(async_pool, minted.storybook_id)
    ctx = row[2][CONTEXT_KEY]
    assert ctx["user_curated"] is False  # fell back to auto
    assert result.moments_count == 5  # 8-moment pool minus 3 superseded


async def test_edit_on_auto_book_still_uses_whole_pool(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 6)
    minted = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", **_urls(),
    )
    result = await edit_storybook(
        db_pool=async_pool, queue=_queue(),
        storybook_id=minted.storybook_id, person_id=pid,
        instructions="warmer colours", prior_instructions=[],
        **_urls(),
    )
    assert result.moments_count == 6
    row = await _fetch_row(async_pool, minted.storybook_id)
    assert row[2][CONTEXT_KEY]["user_curated"] is False
