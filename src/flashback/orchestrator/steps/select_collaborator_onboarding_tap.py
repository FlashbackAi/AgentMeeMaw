"""Collaborator onboarding nudge — an indirect 'defining memory' tap card.

Fires once per session (WM flag) for an active collaborator whose
onboarding phase is still 'onboarding' and whose memory item is
unsatisfied. Reuses the tap-card surface; significance is mined by normal
extraction. No intent gate — nudges every session until the collaborator
records their first memory (then phase flips to 'active')."""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog

from flashback.collaborator_onboarding import (
    get_onboarding_state,
    get_voice_anchor,
    increment_taps_emitted,
)
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.protocol import Tap
from flashback.orchestrator.state import TurnState
from flashback.orchestrator.tap_options import generate_onboarding_tap
from flashback.phase_gate.queries import READ_PERSON_NAME_AND_GENDER

log = structlog.get_logger("flashback.orchestrator")


async def _read_name(deps: OrchestratorDeps, person_id: UUID) -> tuple[str, str | None]:
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(READ_PERSON_NAME_AND_GENDER, {"person_id": person_id})
            row = await cur.fetchone()
    if row is None:
        return "", None
    return str(row[0]), None if row[1] is None else str(row[1])


async def select_collaborator_onboarding_tap(
    state: TurnState, deps: OrchestratorDeps
) -> None:
    with timed_step(log, "select_collaborator_onboarding_tap"):
        if state.user_id is None:
            return
        if state.taps:
            log.info("collaborator_onboarding_tap.skipped", reason="tap_already_set")
            return
        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state
        if wm_state.collaborator_onboarding_tap_emitted:
            return

        async with deps.db_pool.connection() as conn:
            st = await get_onboarding_state(
                conn, person_id=state.person_id, user_id=state.user_id
            )
        if st is None or st.phase != "onboarding" or st.has_memory:
            return

        name, _gender = await _read_name(deps, state.person_id)
        async with deps.db_pool.connection() as conn:
            relationship = await get_voice_anchor(
                conn, person_id=state.person_id, user_id=state.user_id
            )
        # Do NOT fall back to state.person_relationship — that is the
        # subject's relationship descriptor, not this contributor's bond.
        text, options = await generate_onboarding_tap(
            settings=deps.settings, person_name=name, relationship=relationship
        )
        tap = Tap(question_id=uuid4(), text=text, dimension="onboarding", options=options)
        state.taps = [tap]
        await deps.working_memory.record_tap_emitted(
            session_id=str(state.session_id),
            question_id=str(tap.question_id),
            question_text=text,
        )
        await deps.working_memory.update_signals(
            session_id=str(state.session_id),
            collaborator_onboarding_tap_emitted=True,
        )
        async with deps.db_pool.connection() as conn:
            await increment_taps_emitted(
                conn, person_id=state.person_id, user_id=state.user_id
            )
            await conn.commit()
        log.info("collaborator_onboarding_tap.selected", person_id=str(state.person_id))
