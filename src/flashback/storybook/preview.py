"""Build the storybook preview payload (spec 2026-07-05, updated 2026-07-06).

The preview shows the moments a collection's book will draw on (pre-selected)
plus any remainder, so the family can exclude/add before the render. The pool
is now collection-scoped and deterministic: grid collections draw on their
tagged moments (design 2026-07-06), the ``wisdom`` lens draws on the whole
qualifying pool. The curation LLM pass is retired — the picked slice is the
demoted, capped, chronologically-ordered scoped pool.
"""

from __future__ import annotations

from typing import Any

from psycopg_pool import AsyncConnectionPool

from flashback.storybook.collections import COLLECTIONS
from flashback.storybook.generation import (
    StorybookNotFound,
    StorybookTooThin,
    UnknownCollection,
)
from flashback.storybook.repository import (
    STORYBOOK_MAX_SELECT,
    collection_floor,
    demote_used_moments,
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
                cur, person_id=person_id, collection=collection
            )
            whole_pool = await fetch_scope_scene_moments_async(
                cur, person_id=person_id, collection=None
            )
            usage = await fetch_storybook_usage_async(
                cur, person_id=person_id
            )
    floor = collection_floor(collection)
    if len(moments) < floor:
        raise StorybookTooThin(
            len(moments), floor=floor, person_name=person.get("person_name")
        )

    # Two-tier (design 2026-07-06): the collection's tagged moments are
    # pre-picked; the rest of the whole qualifying pool is shown unpicked so
    # the family can ADD a moment the tagger didn't put in this collection
    # (explicit curation — the tag gate above already decided the book is
    # offered). For ``wisdom`` the two sets coincide, so there's nothing extra.
    by_id = {str(m["id"]): m for m in whole_pool}
    for m in moments:  # scoped moments are a subset, but be defensive
        by_id.setdefault(str(m["id"]), m)

    # Deterministic pick: the demoted, capped, chrono-ordered TAGGED pool
    # (exactly what generate_storybook resolves on the auto path).
    picked_ids = [
        str(m["id"])
        for m in demote_used_moments(moments, usage)[:STORYBOOK_MAX_SELECT]
    ]
    picked_set = set(picked_ids)
    # Addable remainder: everything else in the qualifying pool, demoted.
    rest = [
        str(m["id"])
        for m in demote_used_moments(whole_pool, usage)
        if str(m["id"]) not in picked_set
    ]
    ordered = picked_ids + rest
    return {
        "collection": collection,
        "bounds": {
            "min_select": effective_min_select(len(moments), collection),
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
                "collections": list(by_id[mid].get("collections") or []),
                # Deprecated (design 2026-07-06): first tag other than this
                # collection, as a cross-book "also fits" hint. Superseded by
                # the full ``collections`` list above; kept for Node compat.
                "suggested_collection": next(
                    (
                        c
                        for c in (by_id[mid].get("collections") or [])
                        if c != collection
                    ),
                    None,
                ),
                "used_in": usage.get(mid, []),
            }
            for mid in ordered
        ],
    }
