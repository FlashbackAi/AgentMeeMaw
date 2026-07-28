"""build_preview: collection-scoped picks, wisdom whole-pool, used_in chips,
bounds, per-moment collections, and the per-collection floor guard
(design 2026-07-06 — curation retired, tags gate)."""

from __future__ import annotations

import pytest

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


async def _add_moments(pool, person_id: str, tag_lists: list[list[str]]) -> list[str]:
    """Insert one qualifying moment per entry, tagged with that entry's slugs.

    ``None`` entry -> NULL storybook_collections (never-tagged / backfill
    pending); a list (incl. ``[]``) -> that tag array.
    """
    ids: list[str] = []
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                for i, tags in enumerate(tag_lists):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details, storybook_collections) "
                        "VALUES (%s, %s, %s, %s, %s) RETURNING id::text",
                        (person_id, f"m{i}", "n", "the smell of rain", tags),
                    )
                    ids.append((await cur.fetchone())[0])
    return ids


async def test_grid_preview_picks_tagged_and_offers_rest_addable(
    async_pool,
) -> None:
    pid = await _make_person(async_pool)
    ids = await _add_moments(
        async_pool,
        pid,
        [
            ["childhood"],
            ["childhood", "adventurous"],
            ["festivals"],  # not childhood — addable, not picked
            ["childhood"],
            ["childhood"],
            ["childhood"],
            None,  # untagged but qualifying — addable, not picked
        ],
    )
    got = await build_preview(
        db_pool=async_pool, redis=None, settings=object(),
        person_id=pid, collection="childhood",
    )
    assert got["collection"] == "childhood"
    assert got["bounds"] == {"min_select": 5, "max_select": 25}
    rows = got["moments"]
    by_id = {m["id"]: m for m in rows}
    # The 5 childhood-tagged are pre-picked.
    childhood_ids = {ids[0], ids[1], ids[3], ids[4], ids[5]}
    assert {m["id"] for m in rows if m["picked"]} == childhood_ids
    # Two-tier: the festivals-only + untagged moments are shown, NOT picked,
    # so the family can still add them (design 2026-07-06).
    assert by_id[ids[2]]["picked"] is False  # festivals-only
    assert by_id[ids[6]]["picked"] is False  # untagged
    assert {m["id"] for m in rows} == set(ids)  # whole qualifying pool present
    # Picked come first.
    assert all(rows[i]["picked"] for i in range(5))
    assert by_id[ids[1]]["collections"] == ["childhood", "adventurous"]
    assert by_id[ids[1]]["suggested_collection"] == "adventurous"
    assert by_id[ids[2]]["suggested_collection"] == "festivals"


async def test_wisdom_preview_includes_whole_qualifying_pool(async_pool) -> None:
    pid = await _make_person(async_pool)
    # Untagged moments still count for wisdom (whole-pool lens).
    await _add_moments(async_pool, pid, [None, None, None, None])
    got = await build_preview(
        db_pool=async_pool, redis=None, settings=object(),
        person_id=pid, collection="wisdom",
    )
    assert len(got["moments"]) == 4
    assert all(m["picked"] for m in got["moments"])
    assert got["bounds"]["min_select"] == 3  # pool of 4 relaxes the min


async def test_used_in_demotes_and_maps_complete_books(async_pool) -> None:
    pid = await _make_person(async_pool)
    ids = await _add_moments(async_pool, pid, [["childhood"]] * 6)
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
        db_pool=async_pool, redis=None, settings=object(),
        person_id=pid, collection="childhood",
    )
    rows = got["moments"]
    by_id = {m["id"]: m for m in rows}
    assert by_id[ids[0]]["used_in"] == ["festivals"]
    assert by_id[ids[2]]["used_in"] == []  # generating != rendered
    # Moments used in a completed book are demoted to the back.
    assert rows[-1]["id"] in {ids[0], ids[1]}
    assert rows[-2]["id"] in {ids[0], ids[1]}


async def test_grid_below_floor_raises(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_moments(async_pool, pid, [["childhood"]] * 4)  # only 4 < 5
    with pytest.raises(StorybookTooThin) as exc:
        await build_preview(
            db_pool=async_pool, redis=None, settings=object(),
            person_id=pid, collection="childhood",
        )
    assert exc.value.floor == 5


async def test_bad_collection_raises(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_moments(async_pool, pid, [["childhood"]] * 5)
    with pytest.raises(UnknownCollection):
        await build_preview(
            db_pool=async_pool, redis=None, settings=object(),
            person_id=pid, collection="memoir",
        )
