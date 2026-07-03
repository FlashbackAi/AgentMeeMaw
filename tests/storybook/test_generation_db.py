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
    StorybookIdConflict,
    StorybookNotFound,
    StorybookTooThin,
    UnknownCollection,
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


async def _add_qualifying_moments(pool, person_id: str, n: int) -> None:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                for i in range(n):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details) VALUES (%s, %s, %s, %s)",
                        (person_id, f"m{i}", "n", "the smell of rain"),
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
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_MIN_MOMENTS)
    q = _queue()
    result = await generate_storybook(
        db_pool=async_pool, queue=q, person_id=pid,
        collection="childhood",
        anchor_photo_get_url="https://s3.example/anchor?sig=1",
        **_urls(),
    )
    assert result.enqueued is True
    assert result.collection == "childhood"
    assert result.moments_count == STORYBOOK_MIN_MOMENTS
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
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_MIN_MOMENTS)
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
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_MIN_MOMENTS)
    supplied = "11111111-2222-3333-4444-555555555555"
    result = await generate_storybook(
        db_pool=async_pool, queue=None, person_id=pid,
        collection="childhood", storybook_id=supplied, **_urls(),
    )
    assert result.storybook_id == supplied
    assert await _fetch_row(async_pool, supplied) is not None


async def test_duplicate_caller_supplied_id_conflicts(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, STORYBOOK_MIN_MOMENTS)
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
