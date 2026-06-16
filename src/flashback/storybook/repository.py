"""Repository for the ``storybooks`` table + the count-gate watermark.

Async surfaces only -- the storybook is minted from the async Session Wrap
path. 'Qualifying' moment mirrors ``tribute_status`` / ``fetch_scene_moments``:
has ``sensory_details``, a ``time_anchor``, or an ``involves`` edge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Json

# Count-gate constants (CLAUDE.md cold-start cadence family; tunable).
STORYBOOK_MIN_MOMENTS = 3  # floor: never mint a book thinner than this
STORYBOOK_NEW_MOMENTS_THRESHOLD = 8  # new qualifying moments since last edition


@dataclass(frozen=True)
class StorybookGate:
    qualifying_count: int
    last_count: int
    delta: int
    valid: bool


_QUALIFYING_COUNT_SQL = """
SELECT count(*)
  FROM active_moments m
 WHERE m.person_id = %(pid)s
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
"""


async def storybook_gate_async(
    cur,
    *,
    person_id: UUID | str,
    threshold: int = STORYBOOK_NEW_MOMENTS_THRESHOLD,
    min_moments: int = STORYBOOK_MIN_MOMENTS,
) -> StorybookGate:
    """Report whether a new storybook edition should be minted for this person.

    Valid when total qualifying moments >= ``min_moments`` AND the number of
    NEW qualifying moments since the last edition >= ``threshold``.
    """
    await cur.execute(_QUALIFYING_COUNT_SQL, {"pid": str(person_id)})
    row = await cur.fetchone()
    qualifying = int(row[0]) if row is not None else 0

    await cur.execute(
        "SELECT moments_at_last_storybook_run FROM persons WHERE id = %(pid)s",
        {"pid": str(person_id)},
    )
    prow = await cur.fetchone()
    last = int(prow[0]) if prow is not None else 0

    delta = qualifying - last
    valid = qualifying >= min_moments and delta >= threshold
    return StorybookGate(
        qualifying_count=qualifying, last_count=last, delta=delta, valid=valid
    )


async def fetch_person_for_storybook_async(
    cur, *, person_id: UUID | str
) -> dict[str, Any] | None:
    """Return the subject's name/relationship for the assembler."""
    await cur.execute(
        "SELECT name, relationship FROM persons WHERE id = %(pid)s",
        {"pid": str(person_id)},
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {"person_name": row[0], "person_relationship": row[1]}


async def insert_storybook_async(
    cur,
    *,
    person_id: UUID | str,
    title: str | None,
    script: dict[str, Any],
    scene_moment_ids: list[str],
    moments_count: int,
    context: dict[str, Any],
) -> str:
    """Insert a fresh ``generating`` storybook edition; return its id.

    The full storybook context is written on insert (not keyed by kind -- this
    table holds storybooks only), so the artifact job can be pushed immediately.
    """
    await cur.execute(
        """
        INSERT INTO storybooks (
            person_id, title, script, scene_moment_ids, moments_count,
            status, latest_generation_context
        )
        VALUES (
            %(person_id)s, %(title)s, %(script)s, %(scene_ids)s, %(moments_count)s,
            'generating', %(ctx)s
        )
        RETURNING id::text
        """,
        {
            "person_id": str(person_id),
            "title": title,
            "script": Json(script),
            "scene_ids": [str(s) for s in scene_moment_ids],
            "moments_count": moments_count,
            "ctx": json.dumps(context),
        },
    )
    (storybook_id,) = await cur.fetchone()
    return storybook_id


async def set_storybook_status_async(
    cur, *, storybook_id: UUID | str, status: str
) -> None:
    """Advance the lifecycle status."""
    await cur.execute(
        "UPDATE storybooks SET status = %(status)s WHERE id = %(id)s",
        {"id": str(storybook_id), "status": status},
    )


async def stamp_moments_at_last_storybook_run_async(
    cur, *, person_id: UUID | str, count: int
) -> None:
    """Stamp the watermark to the qualifying count captured at gate time."""
    await cur.execute(
        """
        UPDATE persons
           SET moments_at_last_storybook_run = %(count)s
         WHERE id = %(pid)s
        """,
        {"pid": str(person_id), "count": count},
    )
