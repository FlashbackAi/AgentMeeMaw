"""Response Generator wiring."""

from __future__ import annotations

import structlog

from flashback.ground_truth.render import render_ground_truth_block
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState
from flashback.response_generator import ResponseResult, Turn, TurnContext

log = structlog.get_logger("flashback.orchestrator")


async def build_turn_context(
    state: TurnState, deps: OrchestratorDeps
) -> TurnContext:
    """Hydrate person/WM/transcript and build the TurnContext.

    Shared between the JSON ``generate_response`` step and the streaming
    orchestrator path, so the prompt input stays identical regardless of
    transport.
    """
    from flashback.orchestrator.steps.starter_opener import fetch_person

    person = await fetch_person(deps, state.person_id)
    state.person_name = person.name
    state.person_relationship = person.relationship
    state.person_phase = person.phase
    state.person_gender = person.gender or "they"
    wm_state = state.working_memory_state
    if wm_state is None:
        wm_state = await deps.working_memory.get_state(str(state.session_id))
        state.working_memory_state = wm_state
    if not state.transcript:
        state.transcript = await deps.working_memory.get_transcript(
            str(state.session_id)
        )

    # Soft tribute steering. Two channels share the <tribute_gap_hint> slot:
    #  - archetype LEADS (design 2026-06-19): when the open gap is "memories",
    #    pursue a specific thing the contributor hinted at at unlock (e.g. "he
    #    sold a home"). Fires on story AND deepen turns, highest-value first,
    #    each lead at most once per session. The answer never enters the graph;
    #    the elicited moment does (invariant #22).
    #  - checklist GAP hint: the generic next-slot nudge (story turns only,
    #    unchanged shipped behavior), used when no lead applies.
    tribute_gap_hint: str | None = None
    prog = state.tribute_progress
    if prog is not None and not prog.ready:
        first_unfilled = next((s for s in prog.slots if not s.filled), None)
        if (
            first_unfilled is not None
            and first_unfilled.key == "memories"
            and state.effective_intent in ("story", "deepen")
        ):
            from flashback.tribute.leads import lead_hint, pick_next_lead

            lead = pick_next_lead(wm_state.tribute_leads)
            if lead is not None:
                tribute_gap_hint = lead_hint(lead)
                await deps.working_memory.mark_tribute_lead_pursued(
                    str(state.session_id), lead.label
                )
        if (
            tribute_gap_hint is None
            and state.effective_intent == "story"
            and first_unfilled is not None
        ):
            tribute_gap_hint = first_unfilled.hint

    return TurnContext(
        person_name=person.name,
        person_relationship=person.relationship,
        person_gender=state.person_gender,
        ground_truth_block=render_ground_truth_block(
            person.ground_truth, "responder"
        ),
        intent=state.effective_intent,
        emotional_temperature=state.effective_temperature,
        rolling_summary=wm_state.rolling_summary,
        prior_session_summary=wm_state.prior_session_summary,
        recent_turns=[
            Turn(
                role=turn.role,
                content=turn.content,
                timestamp=turn.timestamp,
            )
            for turn in state.transcript
        ],
        related_moments=state.related_moments,
        related_entities=state.related_entities,
        related_threads=state.related_threads,
        mentioned_entities=state.mentioned_entities,
        ambiguous_mention=state.ambiguous_mention,
        seeded_question_text=(
            state.selection.question_text if state.selection else None
        ),
        # Only coverage/promoted taps switch the prompt to acknowledgment-
        # only mode. A ground-truth / anchor tap is a side-capture riding
        # beneath a normal engaged reply (design 2026-06-11 §3b).
        tap_pending=any(t.kind == "coverage" for t in state.taps),
        tap_question_text=next(
            (t.text for t in state.taps if t.kind == "coverage"), None
        ),
        tap_dimension=next(
            (
                t.dimension
                for t in state.taps
                if t.kind == "coverage" and t.dimension
            ),
            None,
        ),
        current_theme_display_name=(
            wm_state.current_theme_display_name
            if wm_state.current_theme_display_name
            else None
        ),
        tribute_gap_hint=tribute_gap_hint,
        mode=state.mode,
    )


async def generate_response(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "generate_response"):
        if deps.response_generator is None:
            state.response = ResponseResult(text="I hear you. Tell me more.")
            log.info("response_generator.skipped", reason="not_configured")
            return
        ctx = await build_turn_context(state, deps)
        state.response = await deps.response_generator.generate_turn_response(ctx)
        log.info(
            "response_generator.completed",
            intent=state.effective_intent,
            reply_length=len(state.response.text),
        )
