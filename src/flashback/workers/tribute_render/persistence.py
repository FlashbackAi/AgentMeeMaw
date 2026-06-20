"""DB reads/writes for the tribute_render worker (sync psycopg over the pool).

Postgres is authoritative: the render context lives on the row; completion flips
status + fires a transactional NOTIFY (sibling of invariant #25).
"""
from __future__ import annotations

import json

from flashback.tribute_video.context import CONTEXT_KEY, RenderContext

NOTIFY_CHANNEL = "tribute_render_complete"


def load_render_context(pool, *, tribute_id: str,
                        composed_at: str = "") -> RenderContext | None:
    """Read the render context off the tributes row. Returns None when missing,
    or stale (a newer composition has superseded this message)."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT person_id::text, status, latest_generation_context "
                "FROM tributes WHERE id = %s",
                (str(tribute_id),),
            )
            row = cur.fetchone()
    if row is None:
        return None
    person_id, _status, lgc = row
    ctx_dict = (lgc or {}).get(CONTEXT_KEY)
    if not ctx_dict:
        return None
    if (composed_at and ctx_dict.get("composed_at")
            and ctx_dict["composed_at"] != composed_at):
        return None  # superseded by a newer /generate
    return RenderContext.from_dict(
        ctx_dict, tribute_id=str(tribute_id), person_id=person_id)


def mark_complete(pool, *, tribute_id: str, person_id: str,
                  video_present: bool, pdf_present: bool) -> None:
    """Flip status -> complete and fire the transactional completion NOTIFY.

    Node (LISTENing) writes video_url + pdf_url from the keys it minted; this
    service never writes the URL columns.
    """
    payload = json.dumps({
        "event": "tribute_render_complete",
        "tribute_id": str(tribute_id),
        "person_id": str(person_id),
        "status": "complete",
        "video_present": video_present,
        "pdf_present": pdf_present,
    })
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tributes SET status = 'complete', rendered_at = now(), "
                "updated_at = now() WHERE id = %s",
                (str(tribute_id),),
            )
            cur.execute("SELECT pg_notify(%s, %s)", (NOTIFY_CHANNEL, payload))
