"""Repository for the ``tributes`` table.

Sync surfaces only in Plan 1 (mirrors the project's test fixtures, which
use a sync psycopg pool). Async surfaces for the HTTP endpoint arrive in
Plan 3 alongside ``POST /tributes/{id}/generate``.
"""

from __future__ import annotations

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
