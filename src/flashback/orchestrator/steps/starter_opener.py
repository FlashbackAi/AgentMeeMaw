"""Session-start step functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.errors import PersonNotFound
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import SessionStartState
from flashback.response_generator import FirstTimeOpenerContext, StarterContext

log = structlog.get_logger("flashback.orchestrator")


def _user_id_str(user_id: UUID | None) -> str:
    """Render an optional user_id for Working Memory storage.

    Defined locally to avoid a circular import with orchestrator.py.
    None becomes "" — never the literal "None". See orchestrator._user_id_str.
    """
    return str(user_id) if user_id else ""


@dataclass(frozen=True)
class PersonRow:
    name: str
    relationship: str | None
    phase: str
    gender: str | None = None
    profile_summary: str | None = None


async def fetch_person(deps: OrchestratorDeps, person_id) -> PersonRow:
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT name, relationship, phase, gender, profile_summary
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
    else:
        name, relationship, phase, gender, profile_summary = row
    return PersonRow(
        name=name,
        relationship=relationship,
        phase=phase,
        gender=gender,
        profile_summary=profile_summary,
    )


async def load_person(state: SessionStartState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "load_person"):
        person = await fetch_person(deps, state.person_id)
        state.person_name = person.name
        state.person_relationship = person.relationship
        state.person_phase = person.phase
        state.person_gender = person.gender or "they"
        # The profile summary is a person-level aggregate across ALL
        # contributors, so seeding it into the opener for a specific
        # contributor (a collaborator) would leak another contributor's
        # content as "last time you talked about…". Only seed it for the
        # creator-era / single-contributor case (no session user_id);
        # contributors with a user_id get continuity scoped to their own
        # moments via load_continuity_context instead.
        if (
            person.profile_summary
            and not state.session_metadata.get("prior_session_summary")
            and not state.user_id
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
        summary = await _build_continuity_summary(
            deps, state.person_id, _user_id_str(state.user_id)
        )
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
                current_user_id=state.user_id,
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
    return StarterContext(
        person_name=state.person_name,
        person_relationship=state.person_relationship,
        person_gender=state.person_gender,
        contributor_display_name=_string_or_none(
            state.session_metadata.get("contributor_display_name")
        ),
        contributor_role=_string_or_none(
            state.session_metadata.get("contributor_role")
            or state.session_metadata.get("role")
        ),
        contributor_voice_anchor=_string_or_none(
            state.session_metadata.get("contributor_voice_anchor")
        ),
        anchor_question_text=anchor_text,
        anchor_dimension=None,
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
            user_id=_user_id_str(state.user_id),
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


async def _build_continuity_summary(
    deps: OrchestratorDeps, person_id, user_id: str = ""
) -> str:
    """Recent continuity for the opener, scoped to the CURRENT contributor.

    Per-contributor continuity (collaborator sub-project): a contributor's
    opener only reflects what THEY have shared — never another
    contributor's "last time you talked about…". Scoped by
    ``told_by_user_id``: a non-empty ``user_id`` matches that contributor's
    own rows; an empty ``user_id`` (creator era) matches ``IS NULL`` rows
    (the creator's own content). A contributor with no own rows yields an
    empty summary, so the opener falls back to a fresh opening.

    Scoped to MOMENTS only — and deliberately so. Per invariant #26,
    ``moments.told_by_user_id`` is the only provenance column that reliably
    means "this contributor shared this". ``entities`` / ``profile_facts``
    carry "first-authored-by" / "whose-session-produced" provenance: the
    profile-summary worker stamps facts with the session's user_id even
    though those facts are derived from the whole (cross-contributor)
    graph. Including them here would leak another contributor's content
    (e.g. a collaborator's first session produces a fact about the
    creator's moment, stamped with the collaborator's id) back into the
    opener — exactly the "last time you talked about…" bug. Moments are
    the substance of continuity anyway.
    """
    if user_id:
        scope = "AND told_by_user_id = %(uid)s"
        params: dict = {"pid": str(person_id), "uid": user_id}
    else:
        scope = "AND told_by_user_id IS NULL"
        params = {"pid": str(person_id)}
    async with deps.db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT title, narrative
                FROM active_moments
                WHERE person_id = %(pid)s
                  {scope}
                ORDER BY created_at DESC
                LIMIT 3
                """,
                params,
            )
            moments = await cur.fetchall()

    lines: list[str] = []
    if moments:
        lines.append("Earlier extracted moments:")
        for title, narrative in moments:
            lines.append(f"- {title}: {narrative}")
    return "\n".join(lines)
