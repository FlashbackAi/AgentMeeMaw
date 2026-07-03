"""DB reads/writes for the storybook_render worker (sync psycopg over the pool).

Postgres is authoritative: the render context lives on the row; completion
flips status + fires a transactional NOTIFY (sibling of invariant #25 and of
tribute_render_complete). Node LISTENs and writes pdf_url + page_urls (and
the cover image_url / thumbnail_url); this service never writes URL columns.
"""
from __future__ import annotations

import json

from flashback.storybook.context import CONTEXT_KEY, StorybookRenderContext

NOTIFY_CHANNEL = "storybook_render_complete"


def load_render_context(pool, *, storybook_id: str,
                        composed_at: str = "") -> StorybookRenderContext | None:
    """Read the render context off the storybooks row. Returns None when
    missing, or stale (a newer composition has superseded this message)."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT person_id::text, status, latest_generation_context "
                "FROM storybooks WHERE id = %s",
                (str(storybook_id),),
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
        return None  # superseded by a newer composition
    return StorybookRenderContext.from_dict(
        ctx_dict, storybook_id=str(storybook_id), person_id=person_id)


def load_saved_script(pool, *, storybook_id: str) -> dict | None:
    """The previously assembled script JSONB (regenerate reuses it)."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT script FROM storybooks WHERE id = %s",
                (str(storybook_id),),
            )
            row = cur.fetchone()
    return row[0] if row and row[0] else None


def save_script(pool, *, storybook_id: str, title: str,
                script_dict: dict) -> None:
    """Persist the assembled script + title on the row (worker-written)."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE storybooks SET title = %s, script = %s, "
                "updated_at = now() WHERE id = %s",
                (title[:200], json.dumps(script_dict), str(storybook_id)),
            )


def mark_complete(pool, *, storybook_id: str, person_id: str, collection: str,
                  pdf_present: bool, pages_present: int,
                  cover_present: bool) -> None:
    """Flip status -> complete and fire the transactional completion NOTIFY.

    Node (LISTENing) writes pdf_url + page_urls (and, when cover_present,
    image_url/thumbnail_url) from the keys it minted. ``pages_present`` is
    how many page PNGs were successfully PUT (Node writes that many URLs).
    """
    payload = json.dumps({
        "event": "storybook_render_complete",
        "storybook_id": str(storybook_id),
        "person_id": str(person_id),
        "collection": collection,
        "status": "complete",
        "pdf_present": pdf_present,
        "pages_present": pages_present,
        "cover_present": cover_present,
    })
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE storybooks SET status = 'complete', "
                "rendered_at = now(), updated_at = now() WHERE id = %s",
                (str(storybook_id),),
            )
            cur.execute("SELECT pg_notify(%s, %s)", (NOTIFY_CHANNEL, payload))


def mark_failed(pool, *, storybook_id: str, error: str) -> None:
    """Mark a render that exhausted its SQS retries as terminally failed.

    Guarded on status='generating' so a newer composition or an already
    complete row is never clobbered. No NOTIFY: the completion handler
    writes URL columns, and there are none to write.
    """
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE storybooks SET status = 'failed', render_error = %s, "
                "updated_at = now() WHERE id = %s AND status = 'generating'",
                (error[:2000], str(storybook_id)),
            )
