"""Load tribute completion progress when the session is in a tribute flow.

Cheap read of the tribute_status view, gated on a current_tribute_id in
Working Memory. Feeds the live meter (/turn metadata) and the soft
gap-steering hint. Best-effort: failures degrade to no progress.
"""

from __future__ import annotations

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState
from flashback.tribute.config_repository import resolve_campaign_db
from flashback.tribute.invitation import resolve_invitation_copy
from flashback.tribute.progress import fetch_tribute_progress_async

log = structlog.get_logger("flashback.orchestrator")


async def load_tribute_progress(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "load_tribute_progress"):
        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state
        tribute_id = wm_state.current_tribute_id
        if not tribute_id:
            return
        async with deps.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                campaign = await resolve_campaign_db(
                    cur, wm_state.current_tribute_campaign or None
                )
                hint = await resolve_invitation_copy(
                    cur,
                    tribute_id=tribute_id,
                    person_id=str(state.person_id),
                    wm_campaign_slug=wm_state.current_tribute_campaign or None,
                )
                state.tribute_progress = await fetch_tribute_progress_async(
                    cur,
                    tribute_id=tribute_id,
                    campaign=campaign,
                    message_hint_override=hint,
                )
