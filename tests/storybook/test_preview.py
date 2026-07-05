"""build_preview: picks-first ordering, wisdom pool-inclusion, used_in
chips, bounds, and thin-pool guard (spec 2026-07-05)."""

from __future__ import annotations

import fakeredis.aioredis
import pytest
import pytest_asyncio

from flashback.storybook import preview as preview_mod
from flashback.storybook.generation import (
    StorybookTooThin,
    UnknownCollection,
)
from flashback.storybook.preview import build_preview


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


async def _pool_ids(pool, person_id: str) -> list[str]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id::text FROM moments WHERE person_id = %s "
                "ORDER BY created_at",
                (person_id,),
            )
            return [r[0] for r in await cur.fetchall()]


@pytest_asyncio.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def fixed_assignments(monkeypatch):
    """Patch the cache layer so no LLM call happens; the returned holder
    lets each test set the assignment (by moment id) after ids exist."""
    holder: dict = {"assignments": {}}

    async def _fake(_redis, **_kwargs):
        return holder["assignments"]

    monkeypatch.setattr(preview_mod, "cached_assignments", _fake)
    return holder


async def test_grid_preview_picks_first_with_hints(
    async_pool, redis, fixed_assignments
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 6)
    ids = await _pool_ids(async_pool, pid)
    fixed_assignments["assignments"] = {
        "childhood": [ids[2], ids[0]],
        "adventurous": [ids[4]],
    }
    got = await build_preview(
        db_pool=async_pool, redis=redis, settings=object(),
        person_id=pid, collection="childhood",
    )
    assert got["collection"] == "childhood"
    assert got["bounds"] == {"min_select": 5, "max_select": 25}
    rows = got["moments"]
    assert [m["id"] for m in rows[:2]] == [ids[2], ids[0]]
    assert rows[0]["picked"] and rows[1]["picked"]
    assert all(not m["picked"] for m in rows[2:])
    by_id = {m["id"]: m for m in rows}
    assert by_id[ids[4]]["suggested_collection"] == "adventurous"
    assert by_id[ids[1]]["suggested_collection"] is None
    assert len(rows) == 6


async def test_wisdom_preview_includes_whole_pool_no_curation(
    async_pool, redis, monkeypatch
) -> None:
    async def _boom(*_a, **_k):
        raise AssertionError("wisdom must not curate")

    monkeypatch.setattr(preview_mod, "cached_assignments", _boom)
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 4)
    got = await build_preview(
        db_pool=async_pool, redis=redis, settings=object(),
        person_id=pid, collection="wisdom",
    )
    assert all(m["picked"] for m in got["moments"])
    assert got["bounds"]["min_select"] == 3  # pool of 4 relaxes the min


async def test_used_in_maps_complete_books(
    async_pool, redis, fixed_assignments
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 5)
    ids = await _pool_ids(async_pool, pid)
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO storybooks (person_id, script, "
                "scene_moment_ids, moments_count, status, collection) "
                "VALUES (%s, '{}', %s, 2, 'complete', 'festivals')",
                (pid, [ids[0], ids[1]]),
            )
            await cur.execute(
                "INSERT INTO storybooks (person_id, script, "
                "scene_moment_ids, moments_count, status, collection) "
                "VALUES (%s, '{}', %s, 1, 'generating', 'nostalgia')",
                (pid, [ids[2]]),
            )
    got = await build_preview(
        db_pool=async_pool, redis=redis, settings=object(),
        person_id=pid, collection="childhood",
    )
    by_id = {m["id"]: m for m in got["moments"]}
    assert by_id[ids[0]]["used_in"] == ["festivals"]
    assert by_id[ids[2]]["used_in"] == []  # generating != rendered


async def test_thin_pool_and_bad_collection_raise(
    async_pool, redis, fixed_assignments
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 2)
    with pytest.raises(StorybookTooThin):
        await build_preview(
            db_pool=async_pool, redis=redis, settings=object(),
            person_id=pid, collection="childhood",
        )
    with pytest.raises(UnknownCollection):
        await build_preview(
            db_pool=async_pool, redis=redis, settings=object(),
            person_id=pid, collection="memoir",
        )
