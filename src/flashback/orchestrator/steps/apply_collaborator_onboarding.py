"""Mirror Node-supplied collaborator onboarding signals at session start."""

from __future__ import annotations

from datetime import datetime

import structlog

from flashback.collaborator_onboarding import upsert_onboarding
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import SessionStartState

log = structlog.get_logger("flashback.orchestrator")


def _parse_ts(value) -> datetime | None:
    """Parse an ISO-8601 timestamp from session_metadata (handles trailing Z)."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def apply_collaborator_onboarding(
    state: SessionStartState, deps: OrchestratorDeps
) -> None:
    """If this is a collaborator session, mirror onboarding signals.

    Upserts the collaborator_onboarding row from session_metadata and stamps
    the resolved voice anchor into state.session_metadata so the opener can
    use it. No-op for the creator (no role='collaborator' or no user_id).
    """
    with timed_step(log, "apply_collaborator_onboarding"):
        meta = state.session_metadata or {}
        if meta.get("role") != "collaborator" or state.user_id is None:
            return

        voice_anchor_text = (meta.get("voice_anchor_text") or "").strip() or None
        voice_anchored_at = _parse_ts(meta.get("voice_anchored_at"))
        # The table CHECK requires both-or-neither; if we have text but no
        # timestamp (or vice versa), pass both or neither.
        if voice_anchor_text and voice_anchored_at is None:
            voice_anchored_at = state.started_at
        if voice_anchored_at is not None and not voice_anchor_text:
            voice_anchored_at = None

        try:
            async with deps.db_pool.connection() as conn:
                await upsert_onboarding(
                    conn,
                    person_id=state.person_id,
                    user_id=state.user_id,
                    voice_anchor_text=voice_anchor_text,
                    voice_anchored_at=voice_anchored_at,
                    modal_answered_at=_parse_ts(meta.get("modal_answered_at")),
                    modal_dismissed_at=_parse_ts(meta.get("modal_dismissed_at")),
                )
                await conn.commit()
        except Exception as exc:  # noqa: BLE001 - onboarding must not break session start
            log.warning(
                "apply_collaborator_onboarding.degraded",
                error=type(exc).__name__,
                detail=str(exc),
            )
            return

        if voice_anchor_text:
            state.session_metadata["contributor_voice_anchor"] = voice_anchor_text
            log.info("collaborator_onboarding.voice_anchor_set")
