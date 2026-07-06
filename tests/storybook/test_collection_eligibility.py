"""Repository: per-collection eligibility counts + collection-scoped fetch
(design 2026-07-06)."""

from __future__ import annotations

from flashback.storybook.repository import (
    STORYBOOK_COLLECTION_FLOOR,
    collection_floor,
    effective_min_select,
    fetch_collection_eligibility_async,
    fetch_scope_scene_moments_async,
)


async def _make_person(pool) -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name, relationship) "
                    "VALUES ('Dad', 'father') RETURNING id::text"
                )
                return (await cur.fetchone())[0]


async def _add(pool, pid: str, tag_lists) -> None:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                for i, tags in enumerate(tag_lists):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details, storybook_collections) "
                        "VALUES (%s, %s, 'n', 'rain', %s)",
                        (pid, f"m{i}", tags),
                    )


def test_floor_and_min_select_by_collection_kind() -> None:
    assert collection_floor("childhood") == STORYBOOK_COLLECTION_FLOOR == 5
    assert collection_floor("wisdom") == 3
    assert effective_min_select(6, "childhood") == 5
    assert effective_min_select(4, "wisdom") == 3
    assert effective_min_select(9, "wisdom") == 5


async def test_eligibility_counts_grid_and_wisdom(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add(
        async_pool,
        pid,
        [
            ["childhood"],
            ["childhood", "festivals"],
            ["childhood"],
            ["childhood"],
            ["childhood"],  # 5 childhood -> eligible
            ["festivals"],  # 2 festivals -> not eligible
            None,           # untagged: counts for wisdom only
        ],
    )
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            elig = await fetch_collection_eligibility_async(cur, person_id=pid)
    assert elig["childhood"] == (5, True)
    assert elig["festivals"] == (2, False)
    assert elig["nostalgia"] == (0, False)
    # wisdom = whole qualifying pool (all 7), eligible at 3.
    assert elig["wisdom"] == (7, True)


async def test_scope_excludes_untagged_and_other_collections(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add(
        async_pool,
        pid,
        [["childhood"], ["festivals"], None, ["childhood"]],
    )
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            childhood = await fetch_scope_scene_moments_async(
                cur, person_id=pid, collection="childhood"
            )
            wisdom = await fetch_scope_scene_moments_async(
                cur, person_id=pid, collection="wisdom"
            )
    assert len(childhood) == 2  # only the two childhood-tagged
    assert all("childhood" in m["collections"] for m in childhood)
    assert len(wisdom) == 4  # whole pool, tags ignored
