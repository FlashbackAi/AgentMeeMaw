"""Session-start step functions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg
import structlog

from flashback.ground_truth.render import render_ground_truth_block
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.errors import PersonNotFound
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import SessionStartState
from flashback.response_generator import FirstTimeOpenerContext, StarterContext

log = structlog.get_logger("flashback.orchestrator")


@dataclass(frozen=True)
class PersonRow:
    name: str
    relationship: str | None
    phase: str
    gender: str | None = None
    profile_summary: str | None = None
    ground_truth: dict = field(default_factory=dict)


async def fetch_person(deps: OrchestratorDeps, person_id) -> PersonRow:
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT name, relationship, phase, gender, profile_summary,
                       ground_truth
                FROM persons
                WHERE id = %s
                """,
                (str(person_id),),
            )
            row = await cur.fetchone()
    if row is None:
        raise PersonNotFound(f"person {person_id} not found")
    if len(row) == 3:
        name, relationship, phase = row
        gender = None
        profile_summary = None
        ground_truth: Any = {}
    elif len(row) == 5:
        name, relationship, phase, gender, profile_summary = row
        ground_truth = {}
    else:
        name, relationship, phase, gender, profile_summary, ground_truth = row
    return PersonRow(
        name=name,
        relationship=relationship,
        phase=phase,
        gender=gender,
        profile_summary=profile_summary,
        ground_truth=ground_truth if isinstance(ground_truth, dict) else {},
    )


async def load_person(state: SessionStartState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "load_person"):
        person = await fetch_person(deps, state.person_id)
        state.person_name = person.name
        state.person_relationship = person.relationship
        state.person_phase = person.phase
        state.person_gender = person.gender or "they"
        state.person_ground_truth = person.ground_truth
        if person.profile_summary and not state.session_metadata.get(
            "prior_session_summary"
        ):
            state.session_metadata["prior_session_summary"] = person.profile_summary


async def load_continuity_context(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> None:
    with timed_step(log, "load_continuity_context"):
        existing = _string_or_none(state.session_metadata.get("prior_session_summary"))
        if existing:
            return
        summary = await _build_continuity_summary(deps, state.person_id)
        if summary:
            state.session_metadata["prior_session_summary"] = summary


async def select_starter_question(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> None:
    """Pick a producer-bank question for the starter-phase opener.

    Only runs in starter phase. If the bank is empty (very first session
    post-onboarding), ``state.selection`` stays None and the opener is
    purely LLM-generated from StarterContext without an anchor question.
    """
    with timed_step(log, "select_starter_question"):
        if state.person_phase != "starter":
            return
        phase_gate = getattr(deps, "phase_gate", None)
        if phase_gate is None:
            log.info("select_starter_question.skipped", reason="no_phase_gate")
            return
        try:
            state.selection = await phase_gate.select_next_question(
                person_id=state.person_id,
                session_id=state.session_id,
                recently_asked_ids=[],
                active_theme_slug=None,
                last_seeded_source=None,
            )
        except Exception as exc:  # noqa: BLE001
            # The opener must not fail just because question selection
            # couldn't run. Leave state.selection unset and continue with
            # an LLM-only opener.
            log.warning(
                "select_starter_question.degraded",
                error=type(exc).__name__,
                detail=str(exc),
            )
            return
        if state.selection and state.selection.question_id is not None:
            log.info(
                "starter_question.selected",
                question_id=str(state.selection.question_id),
                source=state.selection.source,
                rationale=state.selection.rationale,
            )


def build_starter_context(state: SessionStartState) -> StarterContext:
    """Build the StarterContext from SessionStartState.

    Shared between the JSON ``generate_opener`` step and the streaming
    orchestrator path.
    """
    theme_archetype_answers = state.session_metadata.get(
        "theme_archetype_answers"
    ) or []
    if not isinstance(theme_archetype_answers, list):
        theme_archetype_answers = []
    anchor_text: str | None = None
    if state.selection and state.selection.question_text:
        anchor_text = state.selection.question_text
    # An explicit feed pick is one where the caller passed question_id and
    # apply_picked_question resolved it onto state.selection. Contrast with
    # an auto-selected starter question, which the opener may bridge into
    # more loosely.
    picked_id = _string_or_none(state.session_metadata.get("question_id"))
    anchor_is_explicit_pick = bool(
        picked_id
        and state.selection is not None
        and state.selection.question_id is not None
        and str(state.selection.question_id) == picked_id
    )
    return StarterContext(
        person_name=state.person_name,
        person_relationship=state.person_relationship,
        person_gender=state.person_gender,
        ground_truth_block=render_ground_truth_block(
            state.person_ground_truth, "responder"
        ),
        contributor_display_name=_string_or_none(
            state.session_metadata.get("contributor_display_name")
        ),
        contributor_role=_string_or_none(
            state.session_metadata.get("contributor_role")
            or state.session_metadata.get("role")
        ),
        anchor_question_text=anchor_text,
        anchor_dimension=None,
        anchor_is_explicit_pick=anchor_is_explicit_pick,
        prior_session_summary=_string_or_none(
            state.session_metadata.get("prior_session_summary")
        ),
        current_theme_display_name=_string_or_none(
            state.session_metadata.get("current_theme_display_name")
        ),
        current_theme_kind=_string_or_none(
            state.session_metadata.get("current_theme_kind")
        ),
        theme_archetype_answers=[
            a for a in theme_archetype_answers if isinstance(a, dict)
        ],
        mode=state.mode,
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
        ctx = build_starter_context(state)
        state.response = await deps.response_generator.generate_starter_opener(ctx)
        log.info("starter_opener.completed", opener_length=len(state.response.text))


async def generate_first_time_opener(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> None:
    """Generate the opener for the very first session post-onboarding.

    Reads ``archetype_answers`` from ``session_metadata`` first, then
    from ``persons`` as a fallback. Different prompt, different LLM call
    shape from :func:`generate_opener` — and the only place archetype
    answers ever reach the response generator.
    """

    with timed_step(log, "generate_first_time_opener"):
        if deps.response_generator is None:
            state.response = None
            log.info("response_generator.skipped", reason="not_configured")
            return
        ctx = await build_first_time_opener_context(state, deps)
        state.response = await deps.response_generator.generate_first_time_opener(ctx)
        log.info(
            "first_time_opener.completed",
            opener_length=len(state.response.text),
            archetype_answer_count=len(ctx.archetype_answers),
        )


async def build_first_time_opener_context(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> FirstTimeOpenerContext:
    """Build the FirstTimeOpenerContext, including archetype answers.

    Shared between the JSON path and the streaming orchestrator path.
    """
    archetype_answers = await _archetype_answers_for_state(state, deps)
    return FirstTimeOpenerContext(
        person_name=state.person_name,
        person_relationship=state.person_relationship,
        person_gender=state.person_gender,
        contributor_display_name=_string_or_none(
            state.session_metadata.get("contributor_display_name")
        ),
        anchor_question_text=None,
        anchor_dimension=None,
        archetype_answers=archetype_answers,
        mode=state.mode,
    )


async def init_working_memory(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> None:
    with timed_step(log, "init_working_memory"):
        seed_summary = state.session_metadata.get("prior_session_summary", "") or ""
        contributor_display_name = (
            state.session_metadata.get("contributor_display_name", "") or ""
        )
        await deps.working_memory.initialize(
            session_id=str(state.session_id),
            person_id=str(state.person_id),
            role_id=str(state.role_id),
            started_at=state.started_at,
            seed_prior_session_summary=str(seed_summary),
            contributor_display_name=str(contributor_display_name).strip(),
            current_theme_id=str(
                state.session_metadata.get("current_theme_id") or ""
            ),
            current_theme_slug=str(
                state.session_metadata.get("current_theme_slug") or ""
            ),
            current_theme_display_name=str(
                state.session_metadata.get("current_theme_display_name") or ""
            ),
            current_tribute_id=str(
                state.session_metadata.get("current_tribute_id") or ""
            ),
            current_tribute_campaign=str(
                state.session_metadata.get("current_tribute_campaign") or ""
            ),
            tribute_leads=str(
                state.session_metadata.get("tribute_leads") or ""
            ),
            mode=state.mode,
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
                source=state.selection.source,
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


async def _archetype_answers_for_state(
    state: SessionStartState,
    deps: OrchestratorDeps,
) -> list[dict[str, Any]]:
    raw = state.session_metadata.get("archetype_answers")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if deps.db_pool is None:
        return []

    try:
        async with deps.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT COALESCE(archetype_answers, '[]'::jsonb)
                      FROM persons
                     WHERE id = %s
                    """,
                    (str(state.person_id),),
                )
                row = await cur.fetchone()
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        return []

    if row is None or not isinstance(row[0], list):
        return []
    return [item for item in row[0] if isinstance(item, dict)]


async def _build_continuity_summary(deps: OrchestratorDeps, person_id) -> str:
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT title, narrative
                FROM active_moments
                WHERE person_id = %s
                ORDER BY created_at DESC
                LIMIT 3
                """,
                (str(person_id),),
            )
            moments = await cur.fetchall()

            await cur.execute(
                """
                SELECT kind, name, description
                FROM active_entities
                WHERE person_id = %s
                ORDER BY created_at DESC
                LIMIT 5
                """,
                (str(person_id),),
            )
            entities = await cur.fetchall()

            await cur.execute(
                """
                SELECT question_text, answer_text
                FROM active_profile_facts
                WHERE person_id = %s
                ORDER BY created_at DESC
                LIMIT 5
                """,
                (str(person_id),),
            )
            facts = await cur.fetchall()

    lines: list[str] = []
    if moments:
        lines.append("Earlier extracted moments:")
        for title, narrative in moments:
            lines.append(f"- {title}: {narrative}")
    if entities:
        lines.append("Known people, places, and things:")
        for kind, name, description in entities:
            detail = f": {description}" if description else ""
            lines.append(f"- {kind} {name}{detail}")
    if facts:
        lines.append("Known profile facts:")
        for question, answer in facts:
            lines.append(f"- {question} {answer}")
    return "\n".join(lines)
