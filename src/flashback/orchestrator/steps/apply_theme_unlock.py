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
from flashback.tribute.config_repository import fetch_campaign_by_slug
from flashback.tribute.config_schema import campaign_applies
from flashback.tribute.leads import build_leads, leads_to_json
from flashback.tribute.relationships import ensure_relationship_group
from flashback.tribute.repository import (
    ensure_open_tribute_async,
    ensure_standalone_tribute_async,
    merge_tribute_archetype_answers_async,
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
        promoted_draft = False
        tribute_id: str | None = None

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
                    # Resume support: if the caller didn't pass archetype
                    # answers but the user persisted a partial draft, promote
                    # it into the committed answers atomically with the
                    # state flip.
                    if not archetype_answers and theme.archetype_answers_draft:
                        archetype_answers = [
                            a
                            for a in theme.archetype_answers_draft
                            if isinstance(a, dict)
                        ]
                        promoted_draft = True
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
                            promoted_draft=promoted_draft,
                        )
                    # Tribute flow: ensure an open tribute output row exists
                    # for this (person, theme) so the message-capture lane
                    # has somewhere to write. Idempotent within a session.
                    # The campaign resolves FIRST so the open-tribute lookup
                    # is campaign-scoped: each campaign entry gets its own
                    # tribute lifecycle (its own row, its own video), and a
                    # completed prior campaign's tribute is never reopened.
                    if theme.kind == "tribute":
                        # Self-heal the always-on keepsake row before anything
                        # campaign-shaped runs. insert_person seeds it, but a
                        # legacy that lost it stayed lost: nothing re-created
                        # one, so the keepsake meter was simply absent from the
                        # legacy screen until a hand-run backfill. That is what
                        # prod looked like on 2026-07-28 -- 14 legacies with a
                        # campaign card and no keepsake card, every one of them
                        # a row the pre-af3ec20 lookup had adopted and stamped.
                        # Idempotent (returns the existing row when present), so
                        # on the healthy path this is one indexed SELECT.
                        await ensure_standalone_tribute_async(
                            cur, person_id=person_id, theme_id=theme_id
                        )
                        group = await ensure_relationship_group(
                            cur, settings=deps.settings, person_id=person_id
                        )
                        campaign_row = None
                        slug = state.session_metadata.get("campaign")
                        if slug:
                            campaign_row = await fetch_campaign_by_slug(
                                cur, str(slug)
                            )
                            if campaign_row is None:
                                log.info(
                                    "theme_unlock.unknown_campaign_slug",
                                    campaign=str(slug)[:64],
                                )
                            elif not campaign_applies(campaign_row, group):
                                # Relationship targeting (0041): the campaign
                                # doesn't cover this legacy's relationship —
                                # run the neutral tribute flow instead.
                                log.info(
                                    "theme_unlock.campaign_not_for_group",
                                    campaign=str(slug)[:64],
                                    group=group,
                                )
                                campaign_row = None
                        tribute_id = await ensure_open_tribute_async(
                            cur,
                            person_id=person_id,
                            theme_id=theme_id,
                            campaign_id=(
                                campaign_row.id if campaign_row else None
                            ),
                        )
                        # No adoption stamp here. This line is where prod's
                        # keepsake rows got converted (2026-07-28): the open-
                        # tribute lookup used to match campaign_id IS NULL, so a
                        # campaign entry landed on the legacy's STANDALONE row and
                        # this stamped it into a campaign row -- adding a message
                        # slot it was never asked for, which is why finished
                        # videos read 65% + not-ready. The lookup is now
                        # campaign-scoped and ensure_open_tribute stamps at
                        # insert, so a campaign flow always gets its own row.
                        # Per-campaign answers (0042): THIS campaign's
                        # tribute accumulates the answers given under it —
                        # the meter and leads for a new occasion no longer
                        # ride another campaign's answer set.
                        if archetype_answers:
                            await merge_tribute_archetype_answers_async(
                                cur,
                                tribute_id=tribute_id,
                                answers=archetype_answers,
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
        if theme.kind == "tribute" and tribute_id is not None:
            state.session_metadata["current_tribute_id"] = tribute_id
            # Only the ACCEPTED campaign rides Working Memory — a campaign
            # rejected by relationship targeting must not skin the session's
            # copy (invitation text, progress title) either.
            if campaign_row is not None:
                state.session_metadata["current_tribute_campaign"] = (
                    campaign_row.slug
                )
            # Derive in-session steering leads from the archetype answers
            # (design 2026-06-19). They steer the interview; they are never
            # written to the graph (invariant #22).
            leads = build_leads(archetype_answers)
            if leads:
                state.session_metadata["tribute_leads"] = leads_to_json(leads)
