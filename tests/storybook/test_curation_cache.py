"""Valkey-cached curation assignments (spec 2026-07-05).

The cache is keyed by a fingerprint of the qualifying pool's moment ids:
match -> reuse, mismatch/miss -> one curate_moments call, Valkey errors ->
compute inline. Assignments are stored by moment ID so they survive pool
reordering.
"""

from __future__ import annotations

import json

import fakeredis.aioredis
import pytest
import pytest_asyncio

from flashback.storybook import curation_cache
from flashback.storybook.curation_cache import (
    cached_assignments,
    curation_cache_key,
    pool_fingerprint,
)

_MOMENTS = [
    {"id": f"m-{i}", "title": f"t{i}", "narrative": "n"} for i in range(4)
]


@pytest_asyncio.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def fake_curate(monkeypatch):
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return {
            "childhood": [0, 2],
            "interesting": [1],
            "nostalgia": [],
            "festivals": [],
            "adventurous": [3],
        }

    monkeypatch.setattr(curation_cache, "curate_moments", _fake)
    return calls


def test_fingerprint_is_order_insensitive() -> None:
    assert pool_fingerprint(_MOMENTS) == pool_fingerprint(_MOMENTS[::-1])
    assert pool_fingerprint(_MOMENTS) != pool_fingerprint(_MOMENTS[:3])


async def test_miss_curates_and_caches_by_moment_id(
    redis, fake_curate
) -> None:
    got = await cached_assignments(
        redis, settings=object(), person_id="p1", subject_name="Dad",
        relationship="father", moments=_MOMENTS,
    )
    assert got["childhood"] == ["m-0", "m-2"]
    assert len(fake_curate) == 1
    raw = json.loads(await redis.get(curation_cache_key("p1")))
    assert raw["fingerprint"] == pool_fingerprint(_MOMENTS)
    assert raw["assignments"]["adventurous"] == ["m-3"]


async def test_hit_skips_the_llm(redis, fake_curate) -> None:
    await cached_assignments(
        redis, settings=object(), person_id="p1", subject_name="Dad",
        relationship=None, moments=_MOMENTS,
    )
    again = await cached_assignments(
        redis, settings=object(), person_id="p1", subject_name="Dad",
        relationship=None, moments=list(reversed(_MOMENTS)),
    )
    assert len(fake_curate) == 1
    assert again["childhood"] == ["m-0", "m-2"]


async def test_pool_change_recurates(redis, fake_curate) -> None:
    await cached_assignments(
        redis, settings=object(), person_id="p1", subject_name="Dad",
        relationship=None, moments=_MOMENTS,
    )
    grown = _MOMENTS + [{"id": "m-9", "title": "t9", "narrative": "n"}]
    await cached_assignments(
        redis, settings=object(), person_id="p1", subject_name="Dad",
        relationship=None, moments=grown,
    )
    assert len(fake_curate) == 2


async def test_redis_errors_fall_back_to_inline_curation(
    fake_curate,
) -> None:
    class Boom:
        async def get(self, *_a, **_k):
            raise ConnectionError("valkey down")

        async def set(self, *_a, **_k):
            raise ConnectionError("valkey down")

    got = await cached_assignments(
        Boom(), settings=object(), person_id="p1", subject_name="Dad",
        relationship=None, moments=_MOMENTS,
    )
    assert got["childhood"] == ["m-0", "m-2"]
    assert len(fake_curate) == 1
