"""Coverage-gap tap selection for switch and clarify turns."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.protocol import Tap
from flashback.orchestrator.state import TurnState
from flashback.orchestrator.tap_options import generate_tap_options
from flashback.phase_gate.queries import (
    READ_COVERAGE_STATE,
    READ_PERSON_NAME_AND_GENDER,
    SELECT_ANY_COVERAGE_TAP_FOR_DIMENSION,
    SELECT_UNANSWERED_COVERAGE_TAP,
)
from flashback.phase_gate.ranking import TIEBREAKER_DIMENSIONS
from flashback.phase_gate.rendering import render_question_text
from flashback.phase_gate.schema import Dimension

log = structlog.get_logger("flashback.orchestrator")


async def select_coverage_tap(state: TurnState, deps: OrchestratorDeps) -> None:
    """Emit at most one structured tap chip for the lowest empty dimension."""

    with timed_step(log, "select_coverage_tap"):
        if state.intent_result is None or state.intent_result.intent not in {
            "switch",
            "clarify",
        }:
            return

        transcript = state.transcript or await deps.working_memory.get_transcript(
            str(state.session_id)
        )
        state.transcript = transcript

        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state
        if wm_state.taps_emitted_this_session >= 2:
            log.info("coverage_tap.skipped", reason="session_cap")
            return
        if wm_state.user_turns_since_last_tap < 2:
            # Cooldown: don't emit on the user turn immediately after a
            # prior tap. Async extraction means coverage_state hasn't yet
            # absorbed the prior answer, so a back-to-back tap for the
            # same gap dim would be redundant.
            log.info(
                "coverage_tap.skipped",
                reason="cooldown",
                user_turns_since_last_tap=wm_state.user_turns_since_last_tap,
            )
            return

        coverage_state = await _read_coverage_state(deps, state.person_id)
        dimension = _lowest_zero_dimension(coverage_state)
        if dimension is None:
            log.info("coverage_tap.skipped", reason="coverage_complete")
            return

        recent_ids = _parse_uuid_list(
            await deps.working_memory.get_recently_asked_question_ids(
                str(state.session_id)
            )
        )
        recent_ids.extend(_parse_uuid_list(wm_state.emitted_tap_question_ids))
        row = await _fetch_tap_template(
            deps=deps,
            person_id=state.person_id,
            dimension=dimension,
            recent_ids=recent_ids,
            exclude_answered=True,
        )
        if row is None:
            row = await _fetch_tap_template(
                deps=deps,
                person_id=state.person_id,
                dimension=dimension,
                recent_ids=recent_ids,
                exclude_answered=False,
            )
        if row is None:
            log.info(
                "coverage_tap.skipped",
                reason="bank_exhausted",
                dimension=dimension,
            )
            return

        question_id, text = row
        name, gender = await _read_name_and_gender(deps, state.person_id)
        rendered_text = render_question_text(text, name, gender)
        relationship = state.person_relationship or await _read_relationship(deps, state.person_id)
        options = await generate_tap_options(
            settings=deps.settings,
            question_text=rendered_text,
            person_name=name,
            person_relationship=relationship,
            dimension=dimension,
        )
        tap = Tap(
            question_id=question_id,
            text=rendered_text,
            dimension=dimension,
            options=options,
        )
        state.taps = [tap]
        await deps.working_memory.record_tap_emitted(
            session_id=str(state.session_id),
            question_id=str(question_id),
            question_text=rendered_text,
        )
        log.info(
            "coverage_tap.selected",
            question_id=str(question_id),
            dimension=dimension,
        )


async def _read_coverage_state(deps: OrchestratorDeps, person_id: UUID) -> Any:
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(READ_COVERAGE_STATE, {"person_id": person_id})
            row = await cur.fetchone()
    return row[0] if row is not None else {}


async def _read_name_and_gender(
    deps: OrchestratorDeps,
    person_id: UUID,
) -> tuple[str, str | None]:
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(READ_PERSON_NAME_AND_GENDER, {"person_id": person_id})
            row = await cur.fetchone()
    if row is None:
        return "", None
    return str(row[0]), None if row[1] is None else str(row[1])


async def _read_relationship(
    deps: OrchestratorDeps,
    person_id: UUID,
) -> str | None:
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT relationship FROM persons WHERE id = %s",
                (str(person_id),),
            )
            row = await cur.fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0])


async def _fetch_tap_template(
    *,
    deps: OrchestratorDeps,
    person_id: UUID,
    dimension: Dimension,
    recent_ids: list[UUID],
    exclude_answered: bool,
) -> tuple[UUID, str] | None:
    query = (
        SELECT_UNANSWERED_COVERAGE_TAP
        if exclude_answered
        else SELECT_ANY_COVERAGE_TAP_FOR_DIMENSION
    )
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                query,
                {
                    "person_id": person_id,
                    "dimension": dimension,
                    "recent_ids": recent_ids,
                },
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return (row[0], row[1])


def _lowest_zero_dimension(coverage_state: Any) -> Dimension | None:
    if not isinstance(coverage_state, dict):
        coverage_state = {}
    counts = {
        dim: _coverage_count(coverage_state.get(dim, 0))
        for dim in TIEBREAKER_DIMENSIONS
    }
    if all(count > 0 for count in counts.values()):
        return None
    for dim in TIEBREAKER_DIMENSIONS:
        if counts[dim] == 0:
            return cast(Dimension, dim)
    return None


def _coverage_count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _parse_uuid_list(values: list[str]) -> list[UUID]:
    ids: list[UUID] = []
    for value in values:
        try:
            ids.append(UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return ids
