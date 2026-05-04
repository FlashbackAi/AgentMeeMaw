"""Turn orchestration with intent, retrieval, and response generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

import structlog
from psycopg_pool import AsyncConnectionPool

from flashback.config import HttpConfig
from flashback.intent_classifier import IntentClassifier
from flashback.intent_classifier.schema import Intent, Temperature
from flashback.llm.interface import Provider
from flashback.phase_gate import (
    PhaseGate,
    PhaseGateError,
    StarterSelector,
    SteadySelector,
)
from flashback.response_generator import (
    ResponseGenerator,
    StarterContext,
    Turn,
    TurnContext,
)
from flashback.retrieval import EntityResult, MomentResult, RetrievalService, ThreadResult
from flashback.working_memory import WorkingMemory

from flashback.orchestrator.protocol import (
    SessionStartResult,
    SessionWrapResult,
    TurnResult,
)

log = structlog.get_logger("flashback.orchestrator")


class PersonNotFoundError(LookupError):
    """Raised when ``person_id`` doesn't resolve in ``persons``."""


class StarterQuestionNotFoundError(RuntimeError):
    """Raised when the starter-anchor bank is empty."""


class Orchestrator:
    """Coordinates the synchronous turn loop components available so far."""

    def __init__(
        self,
        wm: WorkingMemory,
        db_pool: AsyncConnectionPool,
        settings: HttpConfig | None = None,
        intent_classifier: IntentClassifier | None = None,
        retrieval: RetrievalService | None = None,
        response_generator: ResponseGenerator | None = None,
        phase_gate: PhaseGate | None = None,
    ) -> None:
        self._wm = wm
        self._db = db_pool
        self._settings = settings
        self._intent_classifier = intent_classifier
        self._retrieval = retrieval
        self._response_generator = response_generator
        self._phase_gate = phase_gate

        if self._intent_classifier is None and settings is not None:
            self._intent_classifier = IntentClassifier(
                settings=settings,
                provider=cast(Provider, settings.llm_small_provider),
                model=settings.llm_intent_model,
                timeout=settings.llm_intent_timeout_seconds,
                max_tokens=settings.llm_intent_max_tokens,
            )
        if self._response_generator is None and settings is not None:
            self._response_generator = ResponseGenerator(
                settings=settings,
                provider=cast(Provider, settings.llm_response_provider),
                model=settings.llm_response_model,
                timeout=settings.llm_response_timeout_seconds,
                max_tokens=settings.llm_response_max_tokens,
            )
        if self._phase_gate is None and db_pool is not None:
            self._phase_gate = PhaseGate(
                db_pool=db_pool,
                starter_selector=StarterSelector(db_pool),
                steady_selector=SteadySelector(db_pool, wm),
            )

    async def handle_session_start(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        session_metadata: dict,
    ) -> SessionStartResult:
        _ = (session_id, role_id)
        person = await self._fetch_person(person_id)
        if self._response_generator is None:
            return SessionStartResult(
                opener=f"Tell me about {person.name}.",
                phase=person.phase,
                selected_question_id=None,
            )

        if self._phase_gate is None:
            raise PhaseGateError("phase gate is not configured")
        selection = await self._phase_gate.select_starter_question(person_id)
        if selection.question_id is None or selection.question_text is None:
            raise PhaseGateError("starter selection returned no question")
        if selection.dimension is None:
            raise PhaseGateError("starter selection returned no dimension")
        ctx = StarterContext(
            person_name=person.name,
            person_relationship=person.relationship,
            contributor_role=_string_or_none(
                session_metadata.get("contributor_role")
                or session_metadata.get("role")
            ),
            anchor_question_text=selection.question_text,
            anchor_dimension=selection.dimension,
            prior_session_summary=_string_or_none(
                session_metadata.get("prior_session_summary")
            ),
        )
        result = await self._response_generator.generate_starter_opener(ctx)
        return SessionStartResult(
            opener=result.text,
            phase=person.phase,
            selected_question_id=selection.question_id,
        )

    async def handle_turn(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        user_message: str,
    ) -> TurnResult:
        _ = role_id
        recent_turns = await self._wm.get_transcript(str(session_id))
        state = await self._wm.get_state(str(session_id))

        metadata_intent: str | None = None
        metadata_temperature: str | None = None
        effective_intent: Intent = "story"
        effective_temperature: Temperature = "medium"

        if self._intent_classifier is not None:
            try:
                result = await self._intent_classifier.classify(
                    recent_turns=recent_turns,
                    signals=state.model_dump(),
                )
                await self._wm.update_signals(
                    str(session_id),
                    signal_last_intent=result.intent,
                    signal_emotional_temperature_estimate=(
                        result.emotional_temperature
                    ),
                )
                metadata_intent = result.intent
                metadata_temperature = result.emotional_temperature
                effective_intent = result.intent
                effective_temperature = result.emotional_temperature
                log.info(
                    "intent_classifier.completed",
                    session_id=str(session_id),
                    intent=result.intent,
                    confidence=result.confidence,
                    emotional_temperature=result.emotional_temperature,
                    reasoning=result.reasoning,
                )
            except Exception as e:
                log.warning(
                    "intent_classifier.failed",
                    session_id=str(session_id),
                    error=str(e),
                    exc_info=True,
                )

        moments: list[MomentResult] = []
        entities: list[EntityResult] = []
        threads: list[ThreadResult] = []
        if self._retrieval is not None and effective_intent in {
            "recall",
            "clarify",
            "switch",
        }:
            try:
                moments = await self._retrieval.search_moments(
                    query=user_message,
                    person_id=person_id,
                )
                if effective_intent == "switch":
                    entities = await self._retrieval.get_entities(person_id)
                    threads = await self._retrieval.get_threads(person_id)
                log.info(
                    "retrieval.called",
                    session_id=str(session_id),
                    intent=effective_intent,
                    person_id=str(person_id),
                    n_moments=len(moments),
                    n_entities=len(entities),
                    n_threads=len(threads),
                )
            except Exception as e:
                log.warning(
                    "retrieval.failed",
                    session_id=str(session_id),
                    intent=effective_intent,
                    person_id=str(person_id),
                    error=str(e),
                    exc_info=True,
                )

        if self._response_generator is None:
            return TurnResult(
                reply="I hear you. Tell me more.",
                intent=metadata_intent,
                emotional_temperature=metadata_temperature,
                segment_boundary=False,
            )

        seeded_question_text: str | None = None
        seeded_question_id: UUID | None = None
        if effective_intent == "switch" and self._phase_gate is not None:
            try:
                selection = await self._phase_gate.select_next_question(
                    person_id=person_id,
                    session_id=session_id,
                )
                seeded_question_text = selection.question_text
                seeded_question_id = selection.question_id
                log.info(
                    "phase_gate.selected",
                    session_id=str(session_id),
                    person_id=str(person_id),
                    phase=selection.phase,
                    question_id=(
                        str(selection.question_id)
                        if selection.question_id is not None
                        else None
                    ),
                    source=selection.source,
                    rationale=selection.rationale,
                )
            except Exception as e:
                log.warning(
                    "phase_gate.failed",
                    session_id=str(session_id),
                    person_id=str(person_id),
                    error=str(e),
                    exc_info=True,
                )

        person = await self._fetch_person(person_id)
        ctx = TurnContext(
            person_name=person.name,
            person_relationship=person.relationship,
            intent=effective_intent,
            emotional_temperature=effective_temperature,
            rolling_summary=state.rolling_summary,
            recent_turns=[
                Turn(
                    role=turn.role,
                    content=turn.content,
                    timestamp=turn.timestamp,
                )
                for turn in recent_turns
            ],
            related_moments=moments,
            related_entities=entities,
            related_threads=threads,
            seeded_question_text=seeded_question_text,
        )
        result = await self._response_generator.generate_turn_response(ctx)
        if seeded_question_id is not None:
            await self._wm.append_asked_question(
                str(session_id),
                str(seeded_question_id),
            )
            await self._wm.set_seeded_question(
                str(session_id),
                str(seeded_question_id),
            )
        return TurnResult(
            reply=result.text,
            intent=metadata_intent,
            emotional_temperature=metadata_temperature,
            segment_boundary=False,
        )

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

    async def _fetch_person(self, person_id: UUID) -> "_Person":
        async with self._db.connection() as conn:
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
            raise PersonNotFoundError(f"person {person_id} not found")
        name, relationship, phase = row
        return _Person(name=name, relationship=relationship, phase=phase)

def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class _Person:
    name: str
    relationship: str | None
    phase: str
