"""Theme-unlock side effects executed during ``/session/start``.

When the caller passes ``theme_id`` (and optionally ``archetype_answers``)
in ``session_metadata``, this step:

  1. Looks up the theme and validates it belongs to the caller's person.
  2. If the theme is currently ``locked``, flips it to ``unlocked`` and
     persists the answers (ephemeral priors — only kept on the theme
     row's ``archetype_answers`` JSONB; they don't write moments/traits
     directly).
  3. Records ``current_theme_*`` on ``session_metadata`` so downstream
     steps (init_working_memory, generate_opener, generate_response)
     can surface the theme to the LLM.

Soft-bias only: theme_id never filters question selection or retrieval.
The conversation follows the user once it starts.
"""

from __future__ import annotations

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import SessionStartState
from flashback.themes.repository import (
    fetch_theme_by_id_async,
    unlock_theme_async,
)

log = structlog.get_logger("flashback.orchestrator.apply_theme_unlock")


async def apply_theme_unlock(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> None:
    raw_theme_id = state.session_metadata.get("theme_id")
    if not raw_theme_id:
        return

    with timed_step(log, "apply_theme_unlock"):
        theme_id = str(raw_theme_id)
        person_id = str(state.person_id)
        raw_answers = state.session_metadata.get("archetype_answers") or []
        archetype_answers = [a for a in raw_answers if isinstance(a, dict)]

        async with deps.db_pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    theme = await fetch_theme_by_id_async(
                        cur, theme_id=theme_id, person_id=person_id
                    )
                    if theme is None:
                        log.warning(
                            "theme_unlock.theme_not_found",
                            theme_id=theme_id,
                            person_id=person_id,
                        )
                        return
                    if theme.state == "locked" or archetype_answers:
                        await unlock_theme_async(
                            cur,
                            theme_id=theme_id,
                            archetype_answers=archetype_answers,
                        )
                        log.info(
                            "theme_unlock.flipped_to_unlocked",
                            theme_id=theme_id,
                            slug=theme.slug,
                            answer_count=len(archetype_answers),
                        )

        # Propagate theme context downstream via session_metadata so the
        # opener / WM init / response generator can read it without
        # re-fetching from Postgres.
        state.session_metadata["current_theme_id"] = theme_id
        state.session_metadata["current_theme_slug"] = theme.slug
        state.session_metadata["current_theme_display_name"] = theme.display_name
        state.session_metadata["current_theme_kind"] = theme.kind
        if archetype_answers:
            state.session_metadata["theme_archetype_answers"] = archetype_answers
