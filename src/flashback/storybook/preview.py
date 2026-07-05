"""Build the storybook preview payload (spec 2026-07-05).

The preview shows curation's picks for one collection (pre-selected)
plus the rest of the qualifying pool, so the family can exclude/add
before the render. Grid collections read the fingerprint-cached
curation; ``wisdom`` includes the whole pool with no curation call.
"""

from __future__ import annotations

from typing import Any

from psycopg_pool import AsyncConnectionPool

from flashback.storybook.collections import COLLECTIONS, CURATED_SLUGS
from flashback.storybook.curation_cache import cached_assignments
from flashback.storybook.generation import (
    StorybookNotFound,
    StorybookTooThin,
    UnknownCollection,
)
from flashback.storybook.repository import (
    STORYBOOK_MAX_SELECT,
    STORYBOOK_MIN_MOMENTS,
    effective_min_select,
    fetch_person_for_storybook_async,
    fetch_scope_scene_moments_async,
    fetch_storybook_usage_async,
)

SNIPPET_CHARS = 200


async def build_preview(
    *,
    db_pool: AsyncConnectionPool,
    redis,
    settings: Any,
    person_id: str,
    collection: str,
) -> dict[str, Any]:
    if collection not in COLLECTIONS:
        raise UnknownCollection(collection)
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            person = await fetch_person_for_storybook_async(
                cur, person_id=person_id
            )
            if person is None:
                raise StorybookNotFound(f"person {person_id} not found")
            moments = await fetch_scope_scene_moments_async(
                cur, person_id=person_id
            )
            usage = await fetch_storybook_usage_async(
                cur, person_id=person_id
            )
    if len(moments) < STORYBOOK_MIN_MOMENTS:
        raise StorybookTooThin(
            len(moments), person_name=person.get("person_name")
        )

    by_id = {str(m["id"]): m for m in moments}
    suggested: dict[str, str] = {}
    if collection in CURATED_SLUGS:
        assignments = await cached_assignments(
            redis,
            settings=settings,
            person_id=str(person_id),
            subject_name=person.get("person_name") or "",
            relationship=person.get("person_relationship"),
            moments=moments,
        )
        for slug, ids in assignments.items():
            for mid in ids:
                suggested.setdefault(str(mid), slug)
        picked_ids = [
            str(mid)
            for mid in assignments.get(collection, [])
            if str(mid) in by_id
        ]
    else:  # chapter lens: the whole pool is in by default
        picked_ids = [str(m["id"]) for m in moments]

    picked_set = set(picked_ids)
    ordered = picked_ids + [
        str(m["id"]) for m in moments if str(m["id"]) not in picked_set
    ]
    return {
        "collection": collection,
        "bounds": {
            "min_select": effective_min_select(len(moments)),
            "max_select": STORYBOOK_MAX_SELECT,
        },
        "moments": [
            {
                "id": mid,
                "title": by_id[mid].get("title") or "",
                "snippet": (by_id[mid].get("narrative") or "")[
                    :SNIPPET_CHARS
                ],
                "life_period": by_id[mid].get("life_period") or "",
                "picked": mid in picked_set,
                "suggested_collection": suggested.get(mid),
                "used_in": usage.get(mid, []),
            }
            for mid in ordered
        ],
    }
