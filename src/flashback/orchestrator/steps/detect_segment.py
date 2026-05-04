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
    Run the Segment Detector once the segment buffer threshold is met.

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

    segment_turns = await deps.working_memory.get_segment(str(state.session_id))
    threshold = deps.settings.segment_detector_min_turns
    if len(segment_turns) < threshold:
        log.info(
            "step_skipped",
            step="detect_segment",
            reason="below_buffer_threshold",
            segment_size=len(segment_turns),
            threshold=threshold,
        )
        return

    wm_state = await deps.working_memory.get_state(str(state.session_id))
    prior_rolling_summary = wm_state.rolling_summary or ""

    result = await deps.segment_detector.detect(
        segment_turns=segment_turns,
        prior_rolling_summary=prior_rolling_summary,
        force=False,
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
