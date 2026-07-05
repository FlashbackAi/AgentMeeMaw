"""Valkey-cached storybook curation assignments (spec 2026-07-05).

One Sonnet pass assigns the qualifying pool across all five grid
collections; the result is cached per person, keyed by a fingerprint of
the pool's moment ids. New extracted moments change the fingerprint and
self-invalidate -- no DEL hook anywhere. Cache-aside like the entity-name
cache (invariant #20's pattern); the cache is derived, recomputable state
(invariant #7): any Valkey failure just costs one inline curation call.

Assignments are stored by moment ID, not pool index, so a cached
assignment survives pool reordering between calls.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

from flashback.storybook.curation import curate_moments

log = structlog.get_logger("flashback.storybook.curation_cache")

CURATION_CACHE_TTL_SECONDS = 7 * 24 * 3600


def curation_cache_key(person_id: str) -> str:
    return f"storybook_curation:{person_id}"


def pool_fingerprint(moments: list[dict[str, Any]]) -> str:
    """sha256 over the sorted moment ids -- order-insensitive."""
    ids = sorted(str(m.get("id") or "") for m in moments)
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


async def cached_assignments(
    redis,
    *,
    settings: Any,
    person_id: str,
    subject_name: str,
    relationship: str | None,
    moments: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Grid slug -> ordered moment ids, cache-aside on the fingerprint."""
    key = curation_cache_key(str(person_id))
    fp = pool_fingerprint(moments)
    raw = None
    try:
        raw = await redis.get(key)
    except Exception:
        log.warning("storybook.curation_cache_read_failed", exc_info=True)
    if raw:
        try:
            cached = json.loads(raw)
            if cached.get("fingerprint") == fp:
                return {
                    slug: [str(i) for i in ids]
                    for slug, ids in (cached.get("assignments") or {}).items()
                }
        except (ValueError, AttributeError):
            log.warning("storybook.curation_cache_bad_payload")
    by_index = await curate_moments(
        settings=settings,
        subject_name=subject_name,
        relationship=relationship,
        moments=moments,
    )
    assignments = {
        slug: [str(moments[i]["id"]) for i in idxs]
        for slug, idxs in by_index.items()
    }
    try:
        await redis.set(
            key,
            json.dumps({"fingerprint": fp, "assignments": assignments}),
            ex=CURATION_CACHE_TTL_SECONDS,
        )
    except Exception:
        log.warning("storybook.curation_cache_write_failed", exc_info=True)
    return assignments
