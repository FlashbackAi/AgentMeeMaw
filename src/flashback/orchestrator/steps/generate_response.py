"""Response Generator wiring."""

from __future__ import annotations

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState
from flashback.response_generator import ResponseResult, Turn, TurnContext

log = structlog.get_logger("flashback.orchestrator")


async def generate_response(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "generate_response"):
        if deps.response_generator is None:
            state.response = ResponseResult(text="I hear you. Tell me more.")
            log.info("response_generator.skipped", reason="not_configured")
            return

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

        ctx = TurnContext(
            person_name=person.name,
            person_relationship=person.relationship,
            person_gender=state.person_gender,
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
            tap_pending=bool(state.taps),
            tap_question_text=(state.taps[0].text if state.taps else None),
            tap_dimension=(
                state.taps[0].dimension
                if state.taps and state.taps[0].dimension
                else None
            ),
        )
        state.response = await deps.response_generator.generate_turn_response(ctx)
        log.info(
            "response_generator.completed",
            intent=state.effective_intent,
            reply_length=len(state.response.text),
        )
