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
INSERT INTO tributes (person_id, theme_id, status)
VALUES (%(person_id)s, %(theme_id)s, 'draft')
RETURNING id::text
"""


def insert_tribute_sync(
    cur, *, person_id: UUID | str, theme_id: UUID | str | None = None
) -> str:
    """Insert a fresh draft tribute and return its id."""
    cur.execute(
        _INSERT_TRIBUTE_SQL,
        {
            "person_id": str(person_id),
            "theme_id": str(theme_id) if theme_id is not None else None,
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
) -> str | None:
    """Return the most-recent non-complete tribute id for (person, theme).

    Campaign-scoped: with a ``campaign_id``, an open tribute stamped with
    THAT campaign wins, else an unstamped one (which the caller stamps —
    pre-0039 rows and neutral drafts adopt the campaign on entry). An open
    tribute stamped with a DIFFERENT campaign is never returned — each
    campaign runs its own tribute lifecycle, so one campaign's draft can't
    hijack another campaign's entry (and a completed FD video is never
    reopened by a Friendship Day tap). Without a campaign: any open
    tribute, the pre-CRM behavior.
    """
    campaign_filter = (
        "AND (campaign_id = %(campaign_id)s OR campaign_id IS NULL)"
        if campaign_id
        else ""
    )
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
        },
    )
    row = await cur.fetchone()
    return row[0] if row is not None else None


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
    """
    existing = await fetch_open_tribute_id_async(
        cur, person_id=person_id, theme_id=theme_id, campaign_id=campaign_id
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
    """Return the tribute + its subject's name/relationship for assembly."""
    await cur.execute(
        """
        SELECT tr.id::text, tr.person_id::text, tr.message_text,
               p.name, p.relationship, tr.theme_id::text
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
