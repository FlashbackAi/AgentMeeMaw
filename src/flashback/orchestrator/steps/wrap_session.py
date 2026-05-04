"""Session Wrap orchestrator step."""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.errors import WorkingMemoryNotFound
from flashback.orchestrator.failure_policy import SESSION_WRAP_POLICIES, execute
from flashback.orchestrator.state import SessionWrapState
from flashback.orchestrator.steps.starter_opener import PersonRow, fetch_person
from flashback.queues.client import QueueSendError
from flashback.session_summary.schema import SessionSummaryContext
from flashback.working_memory.client import WorkingMemoryError
from flashback.working_memory.schema import WorkingMemoryState

log = structlog.get_logger("flashback.orchestrator")


async def wrap_session(state: SessionWrapState, deps: OrchestratorDeps) -> None:
    """Run the full Session Wrap sequence."""

    started = time.perf_counter()
    wm_state = await _load_wm_state(state, deps)
    person = await fetch_person(deps, state.person_id)

    await execute(
        policies=SESSION_WRAP_POLICIES,
        step_name="force_close_segment",
        fn=lambda: _force_close_segment(state, deps, wm_state, person),
        state=state,
    )
    wm_state = await _load_wm_state(state, deps)

    await execute(
        policies=SESSION_WRAP_POLICIES,
        step_name="generate_session_summary",
        fn=lambda: _generate_summary(state, deps, wm_state, person),
        state=state,
    )

    await asyncio.gather(
        execute(
            policies=SESSION_WRAP_POLICIES,
            step_name="push_trait_synthesizer",
            fn=lambda: _push_trait_synthesizer(state, deps),
            state=state,
        ),
        execute(
            policies=SESSION_WRAP_POLICIES,
            step_name="push_profile_summary",
            fn=lambda: _push_profile_summary(state, deps),
            state=state,
        ),
        execute(
            policies=SESSION_WRAP_POLICIES,
            step_name="push_producers",
            fn=lambda: _push_producers_per_session(state, deps),
            state=state,
        ),
    )

    wm_state = await _load_wm_state(state, deps)
    state.segments_pushed_count = wm_state.segments_pushed_this_session

    await execute(
        policies=SESSION_WRAP_POLICIES,
        step_name="clear_wm",
        fn=lambda: _clear_wm(state, deps),
        state=state,
    )

    log.info(
        "session_wrap_complete",
        duration_ms=max(1, round((time.perf_counter() - started) * 1000)),
        segments_pushed_count=state.segments_pushed_count,
        final_segment_pushed=state.final_segment_pushed,
        summary_chars=len(state.session_summary_text),
        degraded_steps=list(state.failures.keys()),
    )


async def _load_wm_state(
    state: SessionWrapState,
    deps: OrchestratorDeps,
) -> WorkingMemoryState:
    try:
        return await deps.working_memory.get_state(str(state.session_id))
    except WorkingMemoryError as exc:
        raise WorkingMemoryNotFound(str(exc)) from exc


async def _force_close_segment(
    state: SessionWrapState,
    deps: OrchestratorDeps,
    wm_state: WorkingMemoryState,
    person: PersonRow,
) -> None:
    """Use the existing SegmentDetector with ``force=True``."""

    _ = person
    if deps.segment_detector is None or deps.extraction_queue is None:
        log.info("force_close_segment_skipped", reason="not_configured")
        return

    segment_turns = await deps.working_memory.get_segment(str(state.session_id))
    if not segment_turns:
        log.info("no_open_segment_to_force_close")
        return

    prior_rolling_summary = wm_state.rolling_summary or ""
    result = await deps.segment_detector.detect(
        segment_turns=segment_turns,
        prior_rolling_summary=prior_rolling_summary,
        force=True,
    )
    seeded_question_id = (
        UUID(wm_state.last_seeded_question_id)
        if wm_state.last_seeded_question_id
        else None
    )

    try:
        await deps.extraction_queue.push(
            session_id=state.session_id,
            person_id=state.person_id,
            segment_turns=segment_turns,
            rolling_summary=result.rolling_summary or "",
            prior_rolling_summary=prior_rolling_summary,
            seeded_question_id=seeded_question_id,
        )
    except Exception as exc:
        log.warning(
            "extraction_queue_push_failed",
            error=type(exc).__name__,
            detail=str(exc),
        )
        raise QueueSendError(str(exc)) from exc

    await deps.working_memory.update_rolling_summary(
        str(state.session_id),
        result.rolling_summary or "",
    )
    await deps.working_memory.reset_segment(str(state.session_id))
    await deps.working_memory.set_seeded_question(str(state.session_id), None)
    await deps.working_memory.increment_segments_pushed(str(state.session_id))
    state.final_segment_pushed = True


async def _generate_summary(
    state: SessionWrapState,
    deps: OrchestratorDeps,
    wm_state: WorkingMemoryState,
    person: PersonRow,
) -> None:
    if deps.session_summary_generator is None:
        log.info("session_summary_skipped", reason="not_configured")
        return
    ctx = SessionSummaryContext(
        person_name=person.name,
        relationship=person.relationship,
        rolling_summary=wm_state.rolling_summary or "",
    )
    result = await deps.session_summary_generator.generate(ctx)
    state.session_summary_text = result.text


async def _push_trait_synthesizer(
    state: SessionWrapState,
    deps: OrchestratorDeps,
) -> None:
    if deps.trait_synthesizer_queue is None:
        log.info("trait_synthesizer_push_skipped", reason="not_configured")
        return
    try:
        msg_id = await deps.trait_synthesizer_queue.push(
            person_id=state.person_id,
            session_id=state.session_id,
        )
    except Exception as exc:
        raise QueueSendError(str(exc)) from exc
    state.trait_synthesizer_pushed = True
    log.info("trait_synthesizer_pushed", sqs_message_id=msg_id)


async def _push_profile_summary(
    state: SessionWrapState,
    deps: OrchestratorDeps,
) -> None:
    if deps.profile_summary_queue is None:
        log.info("profile_summary_push_skipped", reason="not_configured")
        return
    try:
        msg_id = await deps.profile_summary_queue.push(
            person_id=state.person_id,
            session_id=state.session_id,
        )
    except Exception as exc:
        raise QueueSendError(str(exc)) from exc
    state.profile_summary_pushed = True
    log.info("profile_summary_pushed", sqs_message_id=msg_id)


async def _push_producers_per_session(
    state: SessionWrapState,
    deps: OrchestratorDeps,
) -> None:
    if deps.producers_per_session_queue is None:
        log.info("producers_per_session_push_skipped", reason="not_configured")
        return
    try:
        msg_id = await deps.producers_per_session_queue.push(
            person_id=state.person_id,
            session_id=state.session_id,
        )
    except Exception as exc:
        raise QueueSendError(str(exc)) from exc
    state.producers_per_session_pushed = True
    log.info("producers_per_session_pushed", sqs_message_id=msg_id)


async def _clear_wm(state: SessionWrapState, deps: OrchestratorDeps) -> None:
    try:
        await deps.working_memory.clear(str(state.session_id))
    except Exception as exc:
        raise QueueSendError(str(exc)) from exc
