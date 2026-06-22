"""Async read/write helpers for the collaborator_onboarding mirror table."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from flashback.collaborator_onboarding.queries import (
    FLIP_PHASE_IF_COMPLETE_SQL,
    GET_ONBOARDING_STATE_SQL,
    GET_VOICE_ANCHOR_SQL,
    INCREMENT_TAPS_EMITTED_SQL,
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
    display_name: str | None = None,
) -> None:
    """Upsert the active onboarding row, mirroring Node session_metadata.

    Never clobbers an existing voice anchor or display_name with NULL
    (COALESCE in SQL). Caller (apply step) must pass voice_anchor_text and
    voice_anchored_at together or both None to satisfy the table CHECK.
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
            "display_name": display_name,
        },
    )


async def get_voice_anchor(conn, *, person_id: UUID, user_id: UUID) -> str | None:
    """Return the active row's voice_anchor_text, or None."""
    cur = await conn.execute(
        GET_VOICE_ANCHOR_SQL, {"person_id": person_id, "user_id": user_id}
    )
    row = await cur.fetchone()
    return row[0] if row else None


@dataclass(frozen=True)
class OnboardingState:
    phase: str
    has_memory: bool
    has_connection: bool
    taps_emitted: int


async def get_onboarding_state(
    conn, *, person_id: UUID, user_id: UUID
) -> OnboardingState | None:
    """Return the active onboarding row as an OnboardingState, or None if absent."""
    cur = await conn.execute(
        GET_ONBOARDING_STATE_SQL, {"person_id": person_id, "user_id": user_id}
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return OnboardingState(
        phase=str(row[0]),
        has_memory=bool(row[1]),
        has_connection=bool(row[2]),
        taps_emitted=int(row[3]),
    )


async def flip_phase_if_complete(
    conn, *, person_id: UUID, user_id: UUID
) -> None:
    """Onboarding Check: flip onboarding->active when both items satisfied.

    Guarded by phase='onboarding' so it is sticky and never double-stamps.
    """
    await conn.execute(
        FLIP_PHASE_IF_COMPLETE_SQL, {"person_id": person_id, "user_id": user_id}
    )


async def increment_taps_emitted(
    conn, *, person_id: UUID, user_id: UUID
) -> None:
    """Increment taps_emitted counter for the active onboarding row."""
    await conn.execute(
        INCREMENT_TAPS_EMITTED_SQL, {"person_id": person_id, "user_id": user_id}
    )
