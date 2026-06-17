"""Async read/write helpers for the collaborator_onboarding mirror table."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from flashback.collaborator_onboarding.queries import (
    GET_VOICE_ANCHOR_SQL,
    UPSERT_ONBOARDING_SQL,
)


async def upsert_onboarding(
    conn,
    *,
    person_id: UUID,
    user_id: UUID,
    voice_anchor_text: str | None = None,
    voice_anchored_at: datetime | None = None,
    modal_answered_at: datetime | None = None,
    modal_dismissed_at: datetime | None = None,
) -> None:
    """Upsert the active onboarding row, mirroring Node session_metadata.

    Never clobbers an existing voice anchor with NULL (COALESCE in SQL).
    Caller (apply step) must pass voice_anchor_text and voice_anchored_at
    together or both None to satisfy the table CHECK.
    """
    await conn.execute(
        UPSERT_ONBOARDING_SQL,
        {
            "person_id": person_id,
            "user_id": user_id,
            "voice_anchor_text": voice_anchor_text,
            "voice_anchored_at": voice_anchored_at,
            "modal_answered_at": modal_answered_at,
            "modal_dismissed_at": modal_dismissed_at,
        },
    )


async def get_voice_anchor(conn, *, person_id: UUID, user_id: UUID) -> str | None:
    """Return the active row's voice_anchor_text, or None."""
    cur = await conn.execute(
        GET_VOICE_ANCHOR_SQL, {"person_id": person_id, "user_id": user_id}
    )
    row = await cur.fetchone()
    return row[0] if row else None
