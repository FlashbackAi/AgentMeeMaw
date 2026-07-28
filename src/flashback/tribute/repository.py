"""Repository for the ``tributes`` table.

Sync surfaces only in Plan 1 (mirrors the project's test fixtures, which
use a sync psycopg pool). Async surfaces for the HTTP endpoint arrive in
Plan 3 alongside ``POST /tributes/{id}/generate``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Json


@dataclass(frozen=True)
class TributeRow:
    id: str
    person_id: str
    theme_id: str | None
    message_text: str | None
    status: str
    video_url: str | None
    image_url: str | None
    thumbnail_url: str | None


_SELECT_TRIBUTE_COLUMNS = (
    "id::text, person_id::text, theme_id::text, message_text, status, "
    "video_url, image_url, thumbnail_url"
)


def _row_to_tribute(row) -> TributeRow:
    (
        tid,
        person_id,
        theme_id,
        message_text,
        status,
        video_url,
        image_url,
        thumbnail_url,
    ) = row
    return TributeRow(
        id=tid,
        person_id=person_id,
        theme_id=theme_id,
        message_text=message_text,
        status=status,
        video_url=video_url,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
    )


_INSERT_TRIBUTE_SQL = """
INSERT INTO tributes (person_id, theme_id, campaign_id, status)
VALUES (%(person_id)s, %(theme_id)s, %(campaign_id)s, 'draft')
RETURNING id::text
"""


def insert_tribute_sync(
    cur,
    *,
    person_id: UUID | str,
    theme_id: UUID | str | None = None,
    campaign_id: UUID | str | None = None,
) -> str:
    """Insert a fresh draft tribute and return its id.

    ``campaign_id`` null = the standalone keepsake meter; set = a campaign
    (occasion) meter (two-meter model, design 2026-07-22)."""
    cur.execute(
        _INSERT_TRIBUTE_SQL,
        {
            "person_id": str(person_id),
            "theme_id": str(theme_id) if theme_id is not None else None,
            "campaign_id": str(campaign_id) if campaign_id is not None else None,
        },
    )
    (tribute_id,) = cur.fetchone()
    return tribute_id


_FETCH_TRIBUTE_SQL = (
    f"SELECT {_SELECT_TRIBUTE_COLUMNS} FROM tributes WHERE id = %(id)s"
)


def fetch_tribute_sync(cur, *, tribute_id: UUID | str) -> TributeRow | None:
    """Return one tribute by id, or None."""
    cur.execute(_FETCH_TRIBUTE_SQL, {"id": str(tribute_id)})
    row = cur.fetchone()
    return _row_to_tribute(row) if row is not None else None


_SET_MESSAGE_SQL = """
UPDATE tributes
   SET message_text = %(message_text)s,
       message_source_turns = %(source_turns)s
 WHERE id = %(id)s
"""


def set_message_sync(
    cur,
    *,
    tribute_id: UUID | str,
    message_text: str,
    source_turns: list[dict[str, Any]] | None = None,
) -> None:
    """Store the polished message + the raw turns it was distilled from."""
    cur.execute(
        _SET_MESSAGE_SQL,
        {
            "id": str(tribute_id),
            "message_text": message_text,
            "source_turns": Json(source_turns) if source_turns is not None else None,
        },
    )


_SET_STATUS_SQL = "UPDATE tributes SET status = %(status)s WHERE id = %(id)s"


def set_status_sync(cur, *, tribute_id: UUID | str, status: str) -> None:
    """Advance the lifecycle status (draft/ready/generating/complete/superseded)."""
    cur.execute(_SET_STATUS_SQL, {"id": str(tribute_id), "status": status})


# ---------------------------------------------------------------------------
# Async surfaces (HTTP route + orchestrator steps)
# ---------------------------------------------------------------------------

_OPEN_STATUSES = ("draft", "ready", "generating")


async def fetch_open_tribute_id_async(
    cur,
    *,
    person_id: UUID | str,
    theme_id: UUID | str,
    campaign_id: str | None = None,
    campaign_slug: str | None = None,
) -> str | None:
    """Return the most-recent non-complete tribute id for (person, theme).

    Campaign-scoped. Prefer ``campaign_slug``: it matches any version of the
    same occasion, because campaigns supersede on every CRM edit (new id,
    version+1) and a tribute is stamped with the version id that was current
    when it was created. Scoping by the exact ``campaign_id`` therefore
    ORPHANS an in-flight tribute the moment its campaign is edited — the bug
    that made unlock_prepare re-ask already-answered archetype questions
    (2026-07-22). Slug scoping mirrors what ``_resolve_render_config`` already
    does (re-resolve by slug). An open tribute stamped with a DIFFERENT slug
    is still never returned. ``campaign_id`` is kept for creation callers
    (ensure_open_tribute) that intentionally pin the current version; without
    either, any open tribute (pre-CRM behavior).
    """
    # A campaign-scoped lookup NEVER adopts a standalone row. Both branches used
    # to carry `OR campaign_id IS NULL`, which predates the two-meter model: once
    # every legacy owned a keepsake row (0048 + the 2026-07-22 backfill), a
    # campaign flow landing on a legacy with no campaign row of its own latched
    # onto the KEEPSAKE and later stamped it — converting it, and so adding a
    # message slot the row had never been asked to fill. Prod 2026-07-28: three
    # keepsakes rendered fine and then read 65% + not-ready with a finished video
    # on them. Campaign and keepsake tributes stay separate rows; the unscoped
    # branch (no campaign at all) still matches anything open.
    if campaign_slug:
        campaign_filter = (
            "AND campaign_id IN (SELECT id FROM tribute_campaigns "
            "WHERE slug = %(campaign_slug)s)"
        )
    elif campaign_id:
        campaign_filter = "AND campaign_id = %(campaign_id)s"
    else:
        campaign_filter = ""
    await cur.execute(
        f"""
        SELECT id::text
          FROM tributes
         WHERE person_id = %(person_id)s
           AND theme_id = %(theme_id)s
           AND status = ANY(%(statuses)s)
           {campaign_filter}
         ORDER BY (campaign_id IS NOT NULL) DESC, created_at DESC
         LIMIT 1
        """,
        {
            "person_id": str(person_id),
            "theme_id": str(theme_id),
            "statuses": list(_OPEN_STATUSES),
            "campaign_id": str(campaign_id) if campaign_id else None,
            "campaign_slug": campaign_slug,
        },
    )
    row = await cur.fetchone()
    return row[0] if row is not None else None


async def ensure_standalone_tribute_async(
    cur,
    *,
    person_id: UUID | str,
    theme_id: UUID | str,
) -> str:
    """Get-or-create the standalone (campaign_id IS NULL) tribute row.

    The always-on keepsake meter (two-meter model, design 2026-07-22). Seeded
    at person creation and backfilled for existing legacies; idempotent (skips
    when a non-superseded standalone row already exists). NOT the same as
    ``ensure_open_tribute_async(campaign_id=None)``, which returns *any* open
    tribute (it would latch onto a campaign row); this targets campaign_id IS
    NULL specifically.
    """
    await cur.execute(
        """
        SELECT id::text FROM tributes
         WHERE person_id = %(pid)s
           AND campaign_id IS NULL
           AND status <> 'superseded'
         ORDER BY created_at DESC
         LIMIT 1
        """,
        {"pid": str(person_id)},
    )
    row = await cur.fetchone()
    if row is not None:
        return row[0]
    await cur.execute(
        """
        INSERT INTO tributes (person_id, theme_id, campaign_id, status)
        VALUES (%(pid)s, %(tid)s, NULL, 'draft')
        RETURNING id::text
        """,
        {"pid": str(person_id), "tid": str(theme_id)},
    )
    (tribute_id,) = await cur.fetchone()
    return tribute_id


async def ensure_open_tribute_async(
    cur,
    *,
    person_id: UUID | str,
    theme_id: UUID | str,
    campaign_id: str | None = None,
) -> str:
    """Return an open tribute for (person, theme[, campaign]), creating a
    draft if none. Idempotent within a session: a second call returns the
    same row. A fresh draft created under a campaign is stamped at insert.

    The LOOKUP resolves the campaign's slug and matches any version of it,
    while creation still pins the current version id. Matching the exact id
    forked a second row for the same occasion every time the CRM republished
    the campaign (10 versions of the friendship-day slug by 2026-07-28), which
    left the gallery showing two cards per legacy — one of them stranded at
    whatever the abandoned row's meter said.
    """
    campaign_slug: str | None = None
    if campaign_id:
        await cur.execute(
            "SELECT slug FROM tribute_campaigns WHERE id = %s", (str(campaign_id),)
        )
        row = await cur.fetchone()
        campaign_slug = row[0] if row else None
    existing = await fetch_open_tribute_id_async(
        cur,
        person_id=person_id,
        theme_id=theme_id,
        campaign_id=None if campaign_slug else campaign_id,
        campaign_slug=campaign_slug,
    )
    if existing is not None:
        return existing
    await cur.execute(
        """
        INSERT INTO tributes (person_id, theme_id, campaign_id, status)
        VALUES (%(person_id)s, %(theme_id)s, %(campaign_id)s, 'draft')
        RETURNING id::text
        """,
        {
            "person_id": str(person_id),
            "theme_id": str(theme_id),
            "campaign_id": str(campaign_id) if campaign_id else None,
        },
    )
    (tribute_id,) = await cur.fetchone()
    return tribute_id


async def merge_tribute_archetype_answers_async(
    cur, *, tribute_id: UUID | str, answers: list[dict]
) -> None:
    """Union committed archetype answers onto the tribute row (0042).

    Per-campaign truth: each campaign's tribute carries the answers given
    under it, so a new occasion's meter and leads never bleed into another
    campaign's. Merged by question_text — stable across bank edits and
    campaigns, unlike the positional q{n} ids — with new answers winning.
    """
    fresh = [a for a in answers if isinstance(a, dict)]
    if not fresh:
        return
    await cur.execute(
        "SELECT archetype_answers FROM tributes WHERE id = %s",
        (str(tribute_id),),
    )
    row = await cur.fetchone()
    current = row[0] if row and isinstance(row[0], list) else []
    merged: dict[str, dict] = {}
    for a in list(current) + fresh:
        if isinstance(a, dict):
            key = str(a.get("question_text") or a.get("question_id") or "")
            if key:
                merged[key] = a
    await cur.execute(
        "UPDATE tributes SET archetype_answers = %s WHERE id = %s",
        (Json(list(merged.values())), str(tribute_id)),
    )


async def fetch_tribute_archetype_answers_async(
    cur, *, tribute_id: UUID | str
) -> list[dict]:
    await cur.execute(
        "SELECT archetype_answers FROM tributes WHERE id = %s",
        (str(tribute_id),),
    )
    row = await cur.fetchone()
    return row[0] if row and isinstance(row[0], list) else []


async def fetch_render_archetype_answers_async(
    cur, *, tribute_id: UUID | str
) -> list[dict]:
    """Committed archetype answers for the render: tribute row, then theme row.

    Both surfaces are written by the unlock flow, but the per-campaign
    slug-scoping means a tribute row can be empty while the theme still holds
    the answers the contributor gave (observed on live rows). The render wants
    whatever the contributor actually said, so try both.
    """
    await cur.execute(
        """
        SELECT CASE
                 WHEN jsonb_typeof(t.archetype_answers) = 'array'
                  AND jsonb_array_length(t.archetype_answers) > 0
                   THEN t.archetype_answers
                 ELSE th.archetype_answers
               END
          FROM tributes t
          LEFT JOIN themes th ON th.id = t.theme_id
         WHERE t.id = %s
        """,
        (str(tribute_id),),
    )
    row = await cur.fetchone()
    return row[0] if row and isinstance(row[0], list) else []


async def fetch_latest_tribute_answers_async(
    cur,
    *,
    person_id: UUID | str,
    theme_id: UUID | str,
    campaign_id: str | None = None,
    campaign_slug: str | None = None,
) -> list[dict]:
    """The most recent SAME-OCCASION tribute's committed answers.

    Used by unlock_prepare when no tribute is open: re-entering a campaign
    after completing its video should prefill the modal from the answers
    given last time, not start blank. Prefer ``campaign_slug`` so the match
    survives campaign version bumps (same reason as
    ``fetch_open_tribute_id_async``); another occasion's answers belong to
    different questions and never match.
    """
    if campaign_slug:
        campaign_filter = (
            "AND campaign_id IN (SELECT id FROM tribute_campaigns "
            "WHERE slug = %(campaign_slug)s)"
        )
    elif campaign_id:
        campaign_filter = "AND campaign_id = %(campaign_id)s"
    else:
        campaign_filter = "AND campaign_id IS NULL"
    await cur.execute(
        f"""
        SELECT archetype_answers
          FROM tributes
         WHERE person_id = %(person_id)s
           AND theme_id = %(theme_id)s
           AND status != 'superseded'
           {campaign_filter}
         ORDER BY created_at DESC
         LIMIT 1
        """,
        {
            "person_id": str(person_id),
            "theme_id": str(theme_id),
            "campaign_id": str(campaign_id) if campaign_id else None,
            "campaign_slug": campaign_slug,
        },
    )
    row = await cur.fetchone()
    return row[0] if row and isinstance(row[0], list) else []


async def fetch_tribute_campaign_id_async(cur, *, tribute_id: UUID | str) -> str | None:
    """The campaign the tribute was created under (stamped at entry), or None."""
    await cur.execute(
        "SELECT campaign_id::text FROM tributes WHERE id = %s",
        (str(tribute_id),),
    )
    row = await cur.fetchone()
    return row[0] if row is not None else None


async def stamp_tribute_campaign_async(
    cur, *, tribute_id: UUID | str, campaign_id: str
) -> None:
    """Stamp the entry campaign once; never overwrites an earlier stamp."""
    await cur.execute(
        "UPDATE tributes SET campaign_id = %s "
        "WHERE id = %s AND campaign_id IS NULL",
        (str(campaign_id), str(tribute_id)),
    )


async def set_message_async(
    cur,
    *,
    tribute_id: UUID | str,
    message_text: str,
    source_turns: list[dict[str, Any]] | None = None,
) -> None:
    """Async twin of ``set_message_sync``."""
    await cur.execute(
        _SET_MESSAGE_SQL,
        {
            "id": str(tribute_id),
            "message_text": message_text,
            "source_turns": Json(source_turns) if source_turns is not None else None,
        },
    )


# ---------------------------------------------------------------------------
# Assembly + generation surfaces (Plan 3)
# ---------------------------------------------------------------------------


async def fetch_scene_moments_async(
    cur, *, person_id: UUID | str, limit: int = 12
) -> list[dict[str, Any]]:
    """Return candidate scene moments (qualifying, newest first) for assembly.

    'Qualifying' mirrors the tribute_status view: has sensory_details, a
    time_anchor, or an involves edge. Returns the fields the assembler and
    the scene-prompt composer need.
    """
    await cur.execute(
        """
        SELECT m.id::text, m.title, m.narrative,
               m.generation_prompt, m.sensory_details, m.time_anchor
          FROM active_moments m
         WHERE m.person_id = %(person_id)s
           AND (
                m.sensory_details IS NOT NULL
             OR m.time_anchor IS NOT NULL
             OR EXISTS (
                 SELECT 1 FROM edges ie
                  WHERE ie.from_kind = 'moment'
                    AND ie.from_id   = m.id
                    AND ie.edge_type = 'involves'
                    AND ie.status    = 'active'
             )
           )
         ORDER BY m.created_at DESC
         LIMIT %(limit)s
        """,
        {"person_id": str(person_id), "limit": limit},
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "narrative": r[2],
            "generation_prompt": r[3],
            "sensory_details": r[4],
            "time_anchor": r[5],
        }
        for r in rows
    ]


async def fetch_theme_scene_moments_async(
    cur, *, person_id: UUID | str, theme_id: UUID | str, limit: int = 15
) -> list[dict[str, Any]]:
    """Qualifying moments tagged to the tribute's theme (the FD-flow story),
    newest first. Empty when the theme has none -- callers fall back to the
    general qualifying pool so a tribute can always assemble."""
    await cur.execute(
        """
        SELECT m.id::text, m.title, m.narrative,
               m.generation_prompt, m.sensory_details, m.time_anchor
          FROM active_moments m
          JOIN edges te ON te.from_kind = 'moment' AND te.from_id = m.id
                       AND te.edge_type = 'themed_as' AND te.to_kind = 'theme'
                       AND te.to_id = %(theme_id)s AND te.status = 'active'
         WHERE m.person_id = %(person_id)s
           AND (
                m.sensory_details IS NOT NULL
             OR m.time_anchor IS NOT NULL
             OR EXISTS (SELECT 1 FROM edges ie
                         WHERE ie.from_kind = 'moment' AND ie.from_id = m.id
                           AND ie.edge_type = 'involves' AND ie.status = 'active')
           )
         ORDER BY m.created_at DESC
         LIMIT %(limit)s
        """,
        {"person_id": str(person_id), "theme_id": str(theme_id), "limit": limit},
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "narrative": r[2],
            "generation_prompt": r[3],
            "sensory_details": r[4],
            "time_anchor": r[5],
        }
        for r in rows
    ]


async def fetch_tribute_for_assembly_async(
    cur, *, tribute_id: UUID | str
) -> dict[str, Any] | None:
    """Return the tribute + its subject's name/relationship for assembly.

    Also carries the render LIFECYCLE (``status``, ``video_url``,
    ``render_composed_at``) because /generate uses this same read as its
    idempotence gate: a repeat click must not start a second paid render.
    ``render_composed_at`` is the tribute_video context's stale-check token,
    i.e. when the current in-flight render was composed.
    """
    await cur.execute(
        """
        SELECT tr.id::text, tr.person_id::text, tr.message_text,
               p.name, p.relationship, tr.theme_id::text,
               p.gender, p.contributor_gender,
               tr.status, tr.video_url,
               tr.latest_generation_context -> 'tribute_video' ->> 'composed_at'
          FROM tributes tr
          JOIN persons p ON p.id = tr.person_id
         WHERE tr.id = %(id)s
        """,
        {"id": str(tribute_id)},
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "person_id": row[1],
        "message_text": row[2],
        "person_name": row[3],
        "person_relationship": row[4],
        "theme_id": row[5],
        "gender": row[6],
        "contributor_gender": row[7],
        "status": row[8],
        "video_url": row[9],
        "render_composed_at": row[10],
    }


async def set_script_async(
    cur,
    *,
    tribute_id: UUID | str,
    script: dict[str, Any],
    scene_moment_ids: list[str],
    checklist_state: dict[str, Any] | None = None,
) -> None:
    """Persist the assembled script + scene id list + checklist snapshot."""
    await cur.execute(
        """
        UPDATE tributes
           SET script = %(script)s,
               scene_moment_ids = %(scene_ids)s,
               checklist_state = %(checklist)s
         WHERE id = %(id)s
        """,
        {
            "id": str(tribute_id),
            "script": Json(script),
            "scene_ids": [str(s) for s in scene_moment_ids],
            "checklist": Json(checklist_state) if checklist_state is not None else None,
        },
    )


async def set_status_async(cur, *, tribute_id: UUID | str, status: str) -> None:
    """Async twin of ``set_status_sync``."""
    await cur.execute(_SET_STATUS_SQL, {"id": str(tribute_id), "status": status})


async def fetch_tribute_generation_context_async(
    cur, *, tribute_id: UUID | str, artifact_kind: str
) -> tuple[str, dict[str, Any] | None] | None:
    """Return ``(person_id, stored_context)`` for one artifact kind on the row.

    ``stored_context`` is the dict previously written under
    ``latest_generation_context[artifact_kind]`` (None when never generated).
    Returns None when the tribute row itself doesn't exist. Used by
    /tributes/{id}/regenerate to re-render from the SAME inputs.
    """
    await cur.execute(
        """
        SELECT person_id::text, latest_generation_context -> %(kind)s
          FROM tributes
         WHERE id = %(id)s
        """,
        {"id": str(tribute_id), "kind": artifact_kind},
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return row[0], row[1]


async def write_tribute_generation_context_async(
    cur,
    *,
    tribute_id: UUID | str,
    artifact_kind: str,
    context: dict[str, Any],
) -> None:
    """Merge a per-artifact-kind context into ``latest_generation_context``.

    The tributes row carries two compiled artifacts (tribute_video +
    storybook). Keying the context by artifact_kind lets both coexist on
    one row and keeps each job's ``composed_at`` stale-check independent:
    writing the storybook context never invalidates an in-flight video job.
    """
    await cur.execute(
        """
        UPDATE tributes
           SET latest_generation_context =
               COALESCE(latest_generation_context, '{}'::jsonb)
               || jsonb_build_object(%(kind)s::text, %(ctx)s::jsonb)
         WHERE id = %(id)s
        """,
        {
            "id": str(tribute_id),
            "kind": artifact_kind,
            "ctx": json.dumps(context),
        },
    )
