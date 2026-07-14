"""Relationship resolver: synonyms -> small-LLM fallback -> cached column."""

from __future__ import annotations

import pytest

from flashback.tribute import relationships
from flashback.tribute.relationships import (
    RELATIONSHIP_GROUPS,
    ensure_relationship_group,
    match_synonym,
)

pytestmark = pytest.mark.asyncio


def _profile(group: str, synonyms: list[str]):
    from flashback.tribute.config_schema import ProfileConfig

    return ProfileConfig(
        id=f"id-{group}",
        group_slug=group,
        display_name=group,
        synonyms=tuple(synonyms),
        voice={},
        opener={},
        art={},
        fallback_opener="x {name}",
        fallback_closing="y {name}",
        archetype_bank=None,
        message_invitation_copy=None,
        deage_cover=False,
        video_target_seconds=None,
        visual_theme_id=None,
        state="published",
        version=1,
    )


def test_match_synonym_case_and_my_prefix() -> None:
    profiles = [
        _profile("parent", ["dad", "father", "amma"]),
        _profile("friend", ["best friend", "friend"]),
    ]
    assert match_synonym("Dad", profiles) == "parent"
    assert match_synonym("  AMMA ", profiles) == "parent"
    assert match_synonym("my best friend", profiles) == "friend"
    assert match_synonym("friend", profiles) == "friend"
    assert match_synonym("colleague", profiles) is None


def test_match_synonym_group_slug_itself_matches() -> None:
    profiles = [_profile("cousin", [])]
    assert match_synonym("Cousin", profiles) == "cousin"


async def _insert_person(pool, relationship: str | None) -> str:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO persons (name, relationship) VALUES ('T', %s) "
                "RETURNING id::text",
                (relationship,),
            )
            return (await cur.fetchone())[0]


async def _group_column(pool, person_id: str) -> str | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT relationship_group FROM persons WHERE id = %s", (person_id,)
            )
            return (await cur.fetchone())[0]


async def test_synonym_hit_writes_back(async_pool) -> None:
    person_id = await _insert_person(async_pool, "Appa")
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            group = await ensure_relationship_group(
                cur, settings=None, person_id=person_id
            )
    assert group == "parent"
    assert await _group_column(async_pool, person_id) == "parent"


async def test_llm_fallback_writes_back(async_pool, monkeypatch) -> None:
    person_id = await _insert_person(async_pool, "chittappa")

    async def fake_classify(settings, label):
        assert label == "chittappa"
        return "other"

    monkeypatch.setattr(relationships, "classify_relationship_llm", fake_classify)
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            group = await ensure_relationship_group(
                cur, settings=None, person_id=person_id
            )
    assert group == "other"
    assert await _group_column(async_pool, person_id) == "other"


async def test_llm_failure_returns_other_without_write(async_pool, monkeypatch) -> None:
    person_id = await _insert_person(async_pool, "the neighbour uncle")

    async def broken_classify(settings, label):
        raise RuntimeError("boom")

    monkeypatch.setattr(relationships, "classify_relationship_llm", broken_classify)
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            group = await ensure_relationship_group(
                cur, settings=None, person_id=person_id
            )
    assert group == "other"
    assert await _group_column(async_pool, person_id) is None  # retries next entry


async def test_cached_column_short_circuits(async_pool, monkeypatch) -> None:
    person_id = await _insert_person(async_pool, "friend")

    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            first = await ensure_relationship_group(
                cur, settings=None, person_id=person_id
            )
    assert first == "friend"

    async def must_not_be_called(settings, label):  # pragma: no cover
        raise AssertionError("LLM must not run when the column is set")

    monkeypatch.setattr(relationships, "classify_relationship_llm", must_not_be_called)
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            second = await ensure_relationship_group(
                cur, settings=None, person_id=person_id
            )
    assert second == "friend"


async def test_empty_relationship_is_other_no_write(async_pool) -> None:
    person_id = await _insert_person(async_pool, None)
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            group = await ensure_relationship_group(
                cur, settings=None, person_id=person_id
            )
    assert group == "other"
    assert await _group_column(async_pool, person_id) is None


def test_groups_registry_shape() -> None:
    assert "friend" in RELATIONSHIP_GROUPS
    assert "other" in RELATIONSHIP_GROUPS
    assert len(RELATIONSHIP_GROUPS) == 8
