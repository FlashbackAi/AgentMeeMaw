"""Intent Classifier wiring."""

from __future__ import annotations

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState

log = structlog.get_logger("flashback.orchestrator")


async def classify(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "intent_classify"):
        state.transcript = await deps.working_memory.get_transcript(
            str(state.session_id)
        )
        state.working_memory_state = await deps.working_memory.get_state(
            str(state.session_id)
        )

        if deps.intent_classifier is None:
            log.info("intent_classifier.skipped", reason="not_configured")
            return

        result = await deps.intent_classifier.classify(
            recent_turns=state.transcript,
            signals=state.working_memory_state.model_dump(),
        )
        state.intent_result = result
        state.effective_intent = result.intent
        state.effective_temperature = result.emotional_temperature
        await deps.working_memory.update_signals(
            str(state.session_id),
            signal_last_intent=result.intent,
            signal_emotional_temperature_estimate=result.emotional_temperature,
        )
        log.info(
            "intent_classifier.completed",
            intent=result.intent,
            confidence=result.confidence,
            emotional_temperature=result.emotional_temperature,
            reasoning=result.reasoning,
        )
