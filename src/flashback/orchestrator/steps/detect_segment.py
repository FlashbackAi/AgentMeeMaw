"""Segment Detector turn step."""

from __future__ import annotations

import time
from uuid import UUID

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.state import TurnState
from flashback.queues.client import QueueSendError

log = structlog.get_logger("flashback.orchestrator")


async def detect_segment(state: TurnState, deps: OrchestratorDeps) -> None:
    """
    Run the Segment Detector on a fixed user-turn cadence.

    Gate: skip unless ``signal_user_turns_since_segment_check`` has
    reached ``segment_detector_user_turn_cadence`` (default 6). One
    "user turn" = one user message + the assistant reply. The counter
    is incremented in ``append_user_turn`` and reset to 0 here on every
    invocation, regardless of whether a boundary fires.

    On boundary, the extraction queue push happens before Working Memory
    mutation so a failed send leaves the segment available for a later
    retry.
    """

    started = time.perf_counter()

    if (
        deps.settings is None
        or deps.segment_detector is None
        or deps.extraction_queue is None
    ):
        log.info(
            "step_skipped",
            step="detect_segment",
            reason="not_configured",
        )
        return

    wm_state = await deps.working_memory.get_state(str(state.session_id))
    cadence = deps.settings.segment_detector_user_turn_cadence
    user_turns_since_check = wm_state.signal_user_turns_since_segment_check
    if user_turns_since_check < cadence:
        log.info(
            "step_skipped",
            step="detect_segment",
            reason="below_user_turn_cadence",
            user_turns_since_check=user_turns_since_check,
            cadence=cadence,
        )
        return

    segment_turns = await deps.working_memory.get_segment(str(state.session_id))
    prior_rolling_summary = wm_state.rolling_summary or ""

    result = await deps.segment_detector.detect(
        segment_turns=segment_turns,
        prior_rolling_summary=prior_rolling_summary,
        force=False,
    )

    await deps.working_memory.reset_user_turns_since_segment_check(
        str(state.session_id),
    )

    if not result.boundary_detected:
        duration_ms = round((time.perf_counter() - started) * 1000)
        log.info(
            "step_complete",
            step="detect_segment",
            duration_ms=max(1, duration_ms),
            boundary=False,
            reasoning=result.reasoning,
        )
        return

    seeded_question_id = (
        UUID(wm_state.last_seeded_question_id)
        if wm_state.last_seeded_question_id
        else None
    )

    try:
        message_id = await deps.extraction_queue.push(
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

    state.segment_boundary_detected = True

    duration_ms = round((time.perf_counter() - started) * 1000)
    log.info(
        "step_complete",
        step="detect_segment",
        duration_ms=max(1, duration_ms),
        boundary=True,
        reasoning=result.reasoning,
        sqs_message_id=message_id,
        seeded_question_id=str(seeded_question_id) if seeded_question_id else None,
    )
