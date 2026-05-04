"""Thin Turn Orchestrator state machine."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import cast
from uuid import UUID, uuid4

import structlog

from flashback.intent_classifier import IntentClassifier
from flashback.llm.interface import Provider
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.errors import (
    PersonNotFound,
    PersonNotFoundError,
    StarterQuestionNotFoundError,
)
from flashback.orchestrator.failure_policy import (
    SESSION_START_POLICIES,
    TURN_POLICIES,
    execute,
)
from flashback.orchestrator.protocol import (
    SessionStartResult,
    SessionWrapResult,
    TurnResult,
)
from flashback.orchestrator.state import SessionStartState, TurnState
from flashback.orchestrator.steps import (
    append_assistant,
    append_opener,
    append_user_turn,
    classify,
    generate_opener,
    generate_response,
    init_working_memory,
    load_person,
    retrieve,
    select_question,
    select_starter_anchor,
)
from flashback.phase_gate import PhaseGate, StarterSelector, SteadySelector
from flashback.response_generator import ResponseGenerator

log = structlog.get_logger("flashback.orchestrator")


class Orchestrator:
    """Coordinates the synchronous turn loop components."""

    owns_working_memory = True

    def __init__(self, deps: OrchestratorDeps | None = None, **legacy_kwargs) -> None:
        if deps is None:
            deps = _deps_from_legacy_kwargs(**legacy_kwargs)
        self._deps = deps

    async def handle_session_start(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        session_metadata: dict,
    ) -> SessionStartResult:
        state = SessionStartState(
            session_id=session_id,
            person_id=person_id,
            role_id=role_id,
            session_metadata=session_metadata,
            started_at=datetime.now(timezone.utc),
        )
        token = structlog.contextvars.bind_contextvars(
            session_id=str(state.session_id),
            person_id=str(state.person_id),
            role_id=str(state.role_id),
        )
        started = time.perf_counter()
        try:
            await execute(
                policies=SESSION_START_POLICIES,
                step_name="load_person",
                fn=lambda: load_person(state, self._deps),
                state=state,
            )
            if self._deps.response_generator is not None:
                await execute(
                    policies=SESSION_START_POLICIES,
                    step_name="select_starter_anchor",
                    fn=lambda: select_starter_anchor(state, self._deps),
                    state=state,
                )
                await execute(
                    policies=SESSION_START_POLICIES,
                    step_name="generate_opener",
                    fn=lambda: generate_opener(state, self._deps),
                    state=state,
                )
            await execute(
                policies=SESSION_START_POLICIES,
                step_name="init_working_memory",
                fn=lambda: init_working_memory(state, self._deps),
                state=state,
            )
            await execute(
                policies=SESSION_START_POLICIES,
                step_name="append_opener",
                fn=lambda: append_opener(state, self._deps),
                state=state,
            )
            duration_ms = max(1, round((time.perf_counter() - started) * 1000))
            log.info(
                "session_start_complete",
                session_id=str(state.session_id),
                person_id=str(state.person_id),
                role_id=str(state.role_id),
                duration_ms=duration_ms,
                phase=state.person_phase,
                question_seeded=(
                    state.selection.question_id is not None
                    if state.selection is not None
                    else False
                ),
                degraded_steps=list(state.failures.keys()),
            )
            return SessionStartResult(
                opener=(
                    state.response.text
                    if state.response is not None
                    else f"Tell me about {state.person_name}."
                ),
                phase=state.person_phase,
                selected_question_id=(
                    state.selection.question_id if state.selection else None
                ),
            )
        finally:
            structlog.contextvars.reset_contextvars(**token)

    async def handle_turn(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        user_message: str,
    ) -> TurnResult:
        state = TurnState(
            turn_id=uuid4(),
            session_id=session_id,
            person_id=person_id,
            role_id=role_id,
            user_message=user_message,
            started_at=datetime.now(timezone.utc),
        )
        token = structlog.contextvars.bind_contextvars(
            turn_id=str(state.turn_id),
            session_id=str(state.session_id),
            person_id=str(state.person_id),
            role_id=str(state.role_id),
        )
        started = time.perf_counter()
        try:
            await execute(
                policies=TURN_POLICIES,
                step_name="append_user_turn",
                fn=lambda: append_user_turn(state, self._deps),
                state=state,
            )
            await execute(
                policies=TURN_POLICIES,
                step_name="intent_classify",
                fn=lambda: classify(state, self._deps),
                state=state,
            )
            if state.effective_intent in {"recall", "clarify", "switch"}:
                await execute(
                    policies=TURN_POLICIES,
                    step_name="retrieve",
                    fn=lambda: retrieve(state, self._deps),
                    state=state,
                )
            if (
                state.effective_intent == "switch"
                and self._deps.response_generator is not None
            ):
                await execute(
                    policies=TURN_POLICIES,
                    step_name="select_question",
                    fn=lambda: select_question(state, self._deps),
                    state=state,
                )
            await execute(
                policies=TURN_POLICIES,
                step_name="generate_response",
                fn=lambda: generate_response(state, self._deps),
                state=state,
            )
            await execute(
                policies=TURN_POLICIES,
                step_name="append_assistant",
                fn=lambda: append_assistant(state, self._deps),
                state=state,
            )

            duration_ms = max(1, round((time.perf_counter() - started) * 1000))
            log.info(
                "turn_complete",
                turn_id=str(state.turn_id),
                session_id=str(state.session_id),
                person_id=str(state.person_id),
                role_id=str(state.role_id),
                duration_ms=duration_ms,
                intent=(
                    state.intent_result.intent if state.intent_result else None
                ),
                question_seeded=(
                    state.selection.question_id is not None
                    if state.selection is not None
                    else False
                ),
                degraded_steps=list(state.failures.keys()),
            )
            return _build_turn_result(state)
        finally:
            structlog.contextvars.reset_contextvars(**token)

    async def handle_session_wrap(
        self,
        session_id: UUID,
        person_id: UUID,
    ) -> SessionWrapResult:
        _ = (session_id, person_id)
        return SessionWrapResult(
            session_summary="",
            moments_extracted_estimate=0,
        )


def _build_turn_result(state: TurnState) -> TurnResult:
    if state.response is None:
        raise RuntimeError("turn completed without a response")
    return TurnResult(
        reply=state.response.text,
        intent=state.intent_result.intent if state.intent_result else None,
        emotional_temperature=(
            state.intent_result.emotional_temperature if state.intent_result else None
        ),
        segment_boundary=False,
    )


def _deps_from_legacy_kwargs(**kwargs) -> OrchestratorDeps:
    wm = kwargs.get("wm")
    db_pool = kwargs.get("db_pool")
    settings = kwargs.get("settings")
    intent_classifier = kwargs.get("intent_classifier")
    retrieval = kwargs.get("retrieval")
    response_generator = kwargs.get("response_generator")
    phase_gate = kwargs.get("phase_gate")

    if intent_classifier is None and settings is not None:
        intent_classifier = IntentClassifier(
            settings=settings,
            provider=cast(Provider, settings.llm_small_provider),
            model=settings.llm_intent_model,
            timeout=settings.llm_intent_timeout_seconds,
            max_tokens=settings.llm_intent_max_tokens,
        )
    if response_generator is None and settings is not None:
        response_generator = ResponseGenerator(
            settings=settings,
            provider=cast(Provider, settings.llm_response_provider),
            model=settings.llm_response_model,
            timeout=settings.llm_response_timeout_seconds,
            max_tokens=settings.llm_response_max_tokens,
        )
    if phase_gate is None and db_pool is not None and wm is not None:
        phase_gate = PhaseGate(
            db_pool=db_pool,
            starter_selector=StarterSelector(db_pool),
            steady_selector=SteadySelector(db_pool, wm),
        )
    return OrchestratorDeps(
        db_pool=db_pool,
        working_memory=wm,
        intent_classifier=intent_classifier,
        retrieval=retrieval,
        phase_gate=phase_gate,
        response_generator=response_generator,
        settings=settings,
    )


__all__ = [
    "Orchestrator",
    "PersonNotFound",
    "PersonNotFoundError",
    "StarterQuestionNotFoundError",
]
