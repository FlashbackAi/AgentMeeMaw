"""Repository for the ``storybooks`` table.

Storybooks are minted ON DEMAND (Node/user-triggered), so there is no count
gate and no ``moments_at_last_storybook_run`` watermark here any more -- that
column (migration 0029) is left in place but unused. A legacy can hold many
storybooks; new ones are inserted, never superseding their predecessors.

'Qualifying' moment mirrors ``tribute_status`` / the themes tier view: has
``sensory_details``, a ``time_anchor``, or an ``involves`` edge. Candidates are
ordered life-chronologically (by ``time_anchor`` year, then creation order) so
a book reads front-to-back as a life rather than newest-extracted-first.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from psycopg.types.json import Json

from flashback.storybook.collections import is_grid

# Whole-pool floor: never mint a book thinner than this many qualifying
# (scoped) moments. Applies to the ``wisdom`` chapter lens, which reads the
# whole pool rather than a tagged slice.
STORYBOOK_MIN_MOMENTS = 3
# Per-grid-collection floor: a grid collection needs at least this many
# qualifying moments TAGGED to it before its book can be minted (design
# 2026-07-06). This is what stops a collection being rendered from moments
# that don't fit it.
STORYBOOK_COLLECTION_FLOOR = 5
# Candidate ceiling handed to the assembler. Larger than the ~12 pages a book
# holds so the LLM has a real pool to curate from.
STORYBOOK_CANDIDATE_LIMIT = 40

# User-confirmed selection bounds (preview flow, spec 2026-07-05). The min
# relaxes to the pool floor when the whole qualifying pool is under 5 so
# thin legacies are not locked out of the preview.
STORYBOOK_MIN_SELECT = 5
STORYBOOK_MAX_SELECT = 25


def collection_floor(collection: str | None) -> int:
    """Minimum qualifying pool size to mint ``collection``.

    Grid collections gate on their tagged slice (``STORYBOOK_COLLECTION_FLOOR``);
    the ``wisdom`` chapter lens (and any unscoped caller) uses the whole-pool
    floor (``STORYBOOK_MIN_MOMENTS``)."""
    return STORYBOOK_COLLECTION_FLOOR if is_grid(collection or "") else STORYBOOK_MIN_MOMENTS


def effective_min_select(pool_size: int, collection: str | None = None) -> int:
    """Minimum confirmable selection for a pool of ``pool_size``.

    Grid collections are gated at ``STORYBOOK_COLLECTION_FLOOR`` (5) up front,
    so their preview min-select is 5. The ``wisdom`` lens may run on a pool of
    3-4, so its min relaxes to the whole-pool floor."""
    if is_grid(collection or ""):
        return STORYBOOK_MIN_SELECT
    if pool_size >= STORYBOOK_MIN_SELECT:
        return STORYBOOK_MIN_SELECT
    return STORYBOOK_MIN_MOMENTS

# Qualifying predicate, shared by every selector below.
_QUALIFYING = """\
m.sensory_details IS NOT NULL
OR m.time_anchor IS NOT NULL
OR EXISTS (
    SELECT 1 FROM edges ie
     WHERE ie.from_kind = 'moment'
       AND ie.from_id   = m.id
       AND ie.edge_type = 'involves'
       AND ie.status    = 'active'
)"""

# Chronological-ish ordering: pull the digits out of time_anchor.year (the
# JSONB shape is {year?, decade?, life_period?, era?}; year may be "1985" or
# "around 1985"), order ascending with undated moments last, then by creation.
_CHRONO_ORDER = """\
ORDER BY
    NULLIF(regexp_replace(COALESCE(m.time_anchor->>'year', ''), '[^0-9]', '', 'g'), '')::int
        ASC NULLS LAST,
    m.created_at ASC"""

_MOMENT_COLUMNS = (
    "m.id::text, m.title, m.narrative, "
    "m.generation_prompt, m.sensory_details, m.time_anchor, "
    "m.life_period_estimate, m.storybook_collections"
)


def _moment_row(r: tuple) -> dict[str, Any]:
    return {
        "id": r[0],
        "title": r[1],
        "narrative": r[2],
        "generation_prompt": r[3],
        "sensory_details": r[4],
        "time_anchor": r[5],
        "life_period": r[6],
        "collections": list(r[7]) if r[7] else [],
    }


async def fetch_scope_scene_moments_async(
    cur,
    *,
    person_id: UUID | str,
    collection: str | None = None,
    theme_id: UUID | str | None = None,
    life_period: str | None = None,
    limit: int = STORYBOOK_CANDIDATE_LIMIT,
) -> list[dict[str, Any]]:
    """Return qualifying candidate moments for a storybook, scope-filtered.

    Optional scope narrows the pool: ``collection`` (a grid slug — moments
    tagged to it in ``storybook_collections``; the ``wisdom`` lens and a None
    collection are unscoped), ``theme_id`` (moments tagged to that theme via an
    active ``themed_as`` edge) and/or ``life_period`` (exact match on
    ``life_period_estimate``). No scope = the whole qualifying pool. Ordered
    life-chronologically and capped at ``limit``.
    """
    params: dict[str, Any] = {"pid": str(person_id), "limit": limit}
    scope_sql = ""
    if collection is not None and is_grid(collection):
        params["collection"] = collection
        scope_sql += "\n           AND %(collection)s = ANY(m.storybook_collections)"
    if theme_id is not None:
        params["theme_id"] = str(theme_id)
        scope_sql += """
           AND EXISTS (
               SELECT 1 FROM edges te
                WHERE te.from_kind = 'moment'
                  AND te.from_id   = m.id
                  AND te.to_kind   = 'theme'
                  AND te.to_id     = %(theme_id)s
                  AND te.edge_type = 'themed_as'
                  AND te.status    = 'active'
           )"""
    if life_period:
        params["life_period"] = life_period
        scope_sql += "\n           AND m.life_period_estimate = %(life_period)s"

    # Read the base table (not the active_moments view) so the
    # storybook_collections column is visible; filter status explicitly.
    await cur.execute(
        f"""
        SELECT {_MOMENT_COLUMNS}
          FROM moments m
         WHERE m.person_id = %(pid)s
           AND m.status = 'active'
           AND ({_QUALIFYING}){scope_sql}
         {_CHRONO_ORDER}
         LIMIT %(limit)s
        """,
        params,
    )
    rows = await cur.fetchall()
    return [_moment_row(r) for r in rows]


def demote_used_moments(
    moments: list[dict[str, Any]], usage: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Move moments already used in a completed book to the back (stable).

    Cross-book repetition control that replaces the retired curation LLM's
    single-assignment rule: a moment carried by another finished storybook is
    deprioritised so a fresh book prefers unseen material, but is NOT excluded
    (the family can still swap it in via the preview). Chrono order is
    preserved within each group.
    """
    fresh = [m for m in moments if not usage.get(str(m["id"]))]
    used = [m for m in moments if usage.get(str(m["id"]))]
    return fresh + used


async def fetch_moments_by_ids_async(
    cur, *, person_id: UUID | str, moment_ids: list[str]
) -> list[dict[str, Any]]:
    """Reload specific moments (active + owned), preserving ``moment_ids`` order.

    Used by regenerate / edit to rebuild the page set from a storybook's stored
    ``scene_moment_ids``. Dropped (superseded / re-owned) ids simply fall out.
    """
    if not moment_ids:
        return []
    await cur.execute(
        f"""
        SELECT {_MOMENT_COLUMNS}
          FROM moments m
         WHERE m.person_id = %(pid)s
           AND m.status = 'active'
           AND m.id = ANY(%(ids)s)
        """,
        {"pid": str(person_id), "ids": [str(m) for m in moment_ids]},
    )
    by_id = {r[0]: _moment_row(r) for r in await cur.fetchall()}
    return [by_id[mid] for mid in (str(m) for m in moment_ids) if mid in by_id]


async def fetch_storybook_usage_async(
    cur, *, person_id: UUID | str
) -> dict[str, list[str]]:
    """moment id -> collection slugs of this person's COMPLETE storybooks.

    Feeds the preview's "also appears in X" chips (spec 2026-07-05);
    informational only, so only rendered (complete) books count.
    """
    await cur.execute(
        """
        SELECT collection, scene_moment_ids
          FROM storybooks
         WHERE person_id = %(pid)s
           AND status = 'complete'
           AND collection IS NOT NULL
        """,
        {"pid": str(person_id)},
    )
    usage: dict[str, list[str]] = {}
    for collection, scene_ids in await cur.fetchall():
        for mid in scene_ids or []:
            slugs = usage.setdefault(str(mid), [])
            if collection not in slugs:
                slugs.append(collection)
    return usage


async def fetch_collection_eligibility_async(
    cur, *, person_id: UUID | str
) -> dict[str, tuple[int, bool]]:
    """Per-collection ``(tagged_qualifying_count, eligible)`` for one person.

    Grid collections count qualifying moments tagged to them and are eligible
    at ``STORYBOOK_COLLECTION_FLOOR``; the ``wisdom`` lens counts the whole
    qualifying pool and is eligible at ``STORYBOOK_MIN_MOMENTS``. Drives the
    ``GET /storybook-collections?person_id=...`` chooser badges.
    """
    from flashback.storybook.collections import COLLECTIONS, TAGGABLE_SLUGS

    # Per-grid-slug qualifying counts (one row per slug present in any array).
    await cur.execute(
        f"""
        SELECT slug, count(*)
          FROM moments m,
               unnest(m.storybook_collections) AS slug
         WHERE m.person_id = %(pid)s
           AND m.status = 'active'
           AND ({_QUALIFYING})
         GROUP BY slug
        """,
        {"pid": str(person_id)},
    )
    grid_counts = {slug: int(n) for slug, n in await cur.fetchall()}

    # Whole qualifying pool — the wisdom lens floor.
    await cur.execute(
        f"""
        SELECT count(*)
          FROM moments m
         WHERE m.person_id = %(pid)s
           AND m.status = 'active'
           AND ({_QUALIFYING})
        """,
        {"pid": str(person_id)},
    )
    (total_qualifying,) = await cur.fetchone()
    total_qualifying = int(total_qualifying)

    out: dict[str, tuple[int, bool]] = {}
    for slug in COLLECTIONS:
        if slug in TAGGABLE_SLUGS:
            count = grid_counts.get(slug, 0)
            out[slug] = (count, count >= STORYBOOK_COLLECTION_FLOOR)
        else:  # wisdom / chapter lens: whole-pool
            out[slug] = (total_qualifying, total_qualifying >= STORYBOOK_MIN_MOMENTS)
    return out


async def fetch_person_for_storybook_async(
    cur, *, person_id: UUID | str
) -> dict[str, Any] | None:
    """Return the subject's name/relationship + genders for the assembler."""
    await cur.execute(
        "SELECT name, relationship, gender, contributor_gender "
        "FROM persons WHERE id = %(pid)s",
        {"pid": str(person_id)},
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {
        "person_name": row[0],
        "person_relationship": row[1],
        "gender": row[2],
        "contributor_gender": row[3],
    }


async def insert_storybook_async(
    cur,
    *,
    person_id: UUID | str,
    title: str | None,
    script: dict[str, Any],
    scene_moment_ids: list[str],
    moments_count: int,
    context: dict[str, Any],
    tags: list[str],
    collection: str | None = None,
    storybook_id: UUID | str | None = None,
) -> str:
    """Insert a fresh ``generating`` storybook; return its id.

    ``storybook_id`` is caller-supplied on the Python render pipeline (Node
    generates it so its presigned S3 keys embed a known id); when omitted the
    DB default mints one. A duplicate id raises psycopg's UniqueViolation --
    the caller maps it to a conflict.
    """
    await cur.execute(
        """
        INSERT INTO storybooks (
            id, person_id, title, script, scene_moment_ids, moments_count,
            status, latest_generation_context, tags, collection
        )
        VALUES (
            COALESCE(%(id)s::uuid, gen_random_uuid()),
            %(person_id)s, %(title)s, %(script)s, %(scene_ids)s, %(moments_count)s,
            'generating', %(ctx)s, %(tags)s, %(collection)s
        )
        RETURNING id::text
        """,
        {
            "id": str(storybook_id) if storybook_id else None,
            "person_id": str(person_id),
            "title": title,
            "script": Json(script),
            "scene_ids": [str(s) for s in scene_moment_ids],
            "moments_count": moments_count,
            "ctx": json.dumps(context),
            "tags": list(tags),
            "collection": collection,
        },
    )
    (storybook_id,) = await cur.fetchone()
    return storybook_id


async def update_storybook_for_rerender_async(
    cur,
    *,
    storybook_id: UUID | str,
    person_id: UUID | str,
    context: dict[str, Any],
) -> bool:
    """Write a fresh render context + flip status back to ``generating``.

    Used by regenerate (reuse_script) and edit (re-assemble) on the Python
    render pipeline. Owned-check inline; returns False when no active row
    matched (missing / unowned / superseded).
    """
    await cur.execute(
        """
        UPDATE storybooks
           SET latest_generation_context = %(ctx)s,
               status = 'generating',
               render_error = NULL,
               updated_at = now()
         WHERE id = %(id)s AND person_id = %(pid)s AND status <> 'superseded'
        """,
        {
            "id": str(storybook_id),
            "pid": str(person_id),
            "ctx": json.dumps(context),
        },
    )
    return cur.rowcount > 0


async def fetch_storybook_for_regen_async(
    cur, *, storybook_id: UUID | str, person_id: UUID | str
) -> dict[str, Any] | None:
    """Return a storybook's script + scene ids + tags for regen/edit, owned-check.

    Returns None when the storybook does not exist, is not owned by this
    person, or has been superseded. ``context`` is the raw
    ``latest_generation_context`` JSONB (may be None) -- the rerender path
    reads ``user_curated`` + the confirmed moment ids from it.
    """
    await cur.execute(
        """
        SELECT title, script, scene_moment_ids, tags, moments_count,
               collection, latest_generation_context
          FROM storybooks
         WHERE id = %(id)s AND person_id = %(pid)s AND status <> 'superseded'
        """,
        {"id": str(storybook_id), "pid": str(person_id)},
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {
        "title": row[0],
        "script": row[1],
        "scene_moment_ids": [str(s) for s in (row[2] or [])],
        "tags": list(row[3] or []),
        "moments_count": row[4],
        "collection": row[5],
        "context": row[6],
    }


async def update_storybook_after_edit_async(
    cur,
    *,
    storybook_id: UUID | str,
    title: str | None,
    script: dict[str, Any],
    scene_moment_ids: list[str],
    context: dict[str, Any],
    tags: list[str],
) -> None:
    """Rewrite the script + context + tags after an edit re-assembly."""
    await cur.execute(
        """
        UPDATE storybooks
           SET title = %(title)s,
               script = %(script)s,
               scene_moment_ids = %(scene_ids)s,
               latest_generation_context = %(ctx)s,
               tags = %(tags)s,
               status = 'generating'
         WHERE id = %(id)s
        """,
        {
            "id": str(storybook_id),
            "title": title,
            "script": Json(script),
            "scene_ids": [str(s) for s in scene_moment_ids],
            "ctx": json.dumps(context),
            "tags": list(tags),
        },
    )


async def update_storybook_after_regen_async(
    cur,
    *,
    storybook_id: UUID | str,
    context: dict[str, Any],
    tags: list[str],
) -> None:
    """Rewrite only the context + tags after a render-only regenerate.

    The script (scenes, captions, ordering) is kept; regenerate re-composes the
    page image prompts with a new preset and may re-tag for template selection.
    """
    await cur.execute(
        """
        UPDATE storybooks
           SET latest_generation_context = %(ctx)s,
               tags = %(tags)s,
               status = 'generating'
         WHERE id = %(id)s
        """,
        {
            "id": str(storybook_id),
            "ctx": json.dumps(context),
            "tags": list(tags),
        },
    )


async def set_storybook_status_async(
    cur, *, storybook_id: UUID | str, status: str
) -> None:
    """Advance the lifecycle status."""
    await cur.execute(
        "UPDATE storybooks SET status = %(status)s WHERE id = %(id)s",
        {"id": str(storybook_id), "status": status},
    )
