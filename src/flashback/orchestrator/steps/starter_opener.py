"""Session-start step functions."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.errors import PersonNotFound
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import SessionStartState
from flashback.phase_gate import PhaseGateError
from flashback.response_generator import StarterContext

log = structlog.get_logger("flashback.orchestrator")


@dataclass(frozen=True)
class PersonRow:
    name: str
    relationship: str | None
    phase: str


async def fetch_person(deps: OrchestratorDeps, person_id) -> PersonRow:
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT name, relationship, phase
                FROM persons
                WHERE id = %s
                """,
                (str(person_id),),
            )
            row = await cur.fetchone()
    if row is None:
        raise PersonNotFound(f"person {person_id} not found")
    name, relationship, phase = row
    return PersonRow(name=name, relationship=relationship, phase=phase)


async def load_person(state: SessionStartState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "load_person"):
        person = await fetch_person(deps, state.person_id)
        state.person_name = person.name
        state.person_relationship = person.relationship
        state.person_phase = person.phase


async def select_starter_anchor(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> None:
    with timed_step(log, "select_starter_anchor"):
        if deps.phase_gate is None:
            raise PhaseGateError("phase gate is not configured")
        state.selection = await deps.phase_gate.select_starter_question(
            state.person_id
        )
        if state.selection.question_id is None or state.selection.question_text is None:
            raise PhaseGateError("starter selection returned no question")
        if state.selection.dimension is None:
            raise PhaseGateError("starter selection returned no dimension")
        log.info(
            "phase_gate.starter_selected",
            phase=state.selection.phase,
            question_id=str(state.selection.question_id),
            source=state.selection.source,
            dimension=state.selection.dimension,
            rationale=state.selection.rationale,
        )


async def generate_opener(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> None:
    with timed_step(log, "generate_opener"):
        if deps.response_generator is None:
            state.response = None
            log.info("response_generator.skipped", reason="not_configured")
            return
        if state.selection is None or state.selection.question_text is None:
            raise PhaseGateError("starter selection missing before opener generation")
        if state.selection.dimension is None:
            raise PhaseGateError("starter selection missing dimension")
        ctx = StarterContext(
            person_name=state.person_name,
            person_relationship=state.person_relationship,
            contributor_role=_string_or_none(
                state.session_metadata.get("contributor_role")
                or state.session_metadata.get("role")
            ),
            anchor_question_text=state.selection.question_text,
            anchor_dimension=state.selection.dimension,
            prior_session_summary=_string_or_none(
                state.session_metadata.get("prior_session_summary")
            ),
        )
        state.response = await deps.response_generator.generate_starter_opener(ctx)
        log.info("starter_opener.completed", opener_length=len(state.response.text))


async def init_working_memory(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> None:
    with timed_step(log, "init_working_memory"):
        seed_summary = state.session_metadata.get("prior_session_summary", "") or ""
        await deps.working_memory.initialize(
            session_id=str(state.session_id),
            person_id=str(state.person_id),
            role_id=str(state.role_id),
            started_at=state.started_at,
            seed_rolling_summary=str(seed_summary),
        )


async def append_opener(state: SessionStartState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "append_opener"):
        opener = (
            state.response.text
            if state.response is not None
            else f"Tell me about {state.person_name}."
        )
        await deps.working_memory.append_turn(
            session_id=str(state.session_id),
            role="assistant",
            content=opener,
            timestamp=state.started_at,
        )
        await deps.working_memory.update_signals(
            session_id=str(state.session_id),
            last_opener=opener,
        )
        if state.selection and state.selection.question_id is not None:
            question_id = str(state.selection.question_id)
            await deps.working_memory.set_seeded_question(
                session_id=str(state.session_id),
                question_id=question_id,
            )
            await deps.working_memory.append_asked_question(
                session_id=str(state.session_id),
                question_id=question_id,
            )


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
