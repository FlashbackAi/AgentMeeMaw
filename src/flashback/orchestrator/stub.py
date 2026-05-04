"""
Step-4 placeholder orchestrator.

Returns canned responses but participates correctly in the integration
shape: reads ``persons.name`` from Postgres for the opener, reads /
writes Working Memory the same way the real Turn Orchestrator (step 9)
will, and surfaces the same return types. When step 9 lands,
:class:`StubOrchestrator` is replaced with the real implementation —
nothing else needs to change.

Note: appending the user/assistant turns to Working Memory and clearing
WM on wrap stay in the HTTP layer; the orchestrator is responsible for
the *content* of the response, not for sequencing the WM writes around
it. That separation matches the step-9 design (the Turn Orchestrator
calls Phase Gate / Intent Classifier / Response Generator / Segment
Detector; the Conversation Gateway brackets it with WM hydration).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

import structlog
from psycopg_pool import AsyncConnectionPool

from flashback.config import HttpConfig
from flashback.intent_classifier import IntentClassifier
from flashback.llm.interface import Provider
from flashback.retrieval import RetrievalService
from flashback.working_memory import WorkingMemory

log = structlog.get_logger("flashback.orchestrator.stub")


# --- Result shapes ----------------------------------------------------------


@dataclass(frozen=True)
class SessionStartResult:
    opener: str
    phase: str
    selected_question_id: str | None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    intent: str | None
    emotional_temperature: str | None
    segment_boundary: bool


@dataclass(frozen=True)
class SessionWrapResult:
    session_summary: str
    moments_extracted_estimate: int


# --- Protocol ---------------------------------------------------------------


class Orchestrator(Protocol):
    """The interface the HTTP routes consume.

    Step 9 replaces :class:`StubOrchestrator` with the real Turn
    Orchestrator. The HTTP layer programs against this Protocol, so the
    swap is a one-line dependency change in :mod:`flashback.http.deps`.
    """

    async def handle_session_start(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        session_metadata: dict,
    ) -> SessionStartResult: ...

    async def handle_turn(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        user_message: str,
    ) -> TurnResult: ...

    async def handle_session_wrap(
        self,
        session_id: UUID,
        person_id: UUID,
    ) -> SessionWrapResult: ...


# --- Stub implementation ----------------------------------------------------


class PersonNotFoundError(LookupError):
    """Raised when ``person_id`` doesn't resolve in ``persons``.

    The HTTP layer maps this to 404. Kept as a domain-layer exception
    so the orchestrator stays decoupled from FastAPI.
    """


class StubOrchestrator:
    """Step-4 placeholder. Step 9 replaces this body."""

    def __init__(
        self,
        wm: WorkingMemory,
        db_pool: AsyncConnectionPool,
        settings: HttpConfig | None = None,
        intent_classifier: IntentClassifier | None = None,
        retrieval: RetrievalService | None = None,
    ) -> None:
        self._wm = wm
        self._db = db_pool
        self._settings = settings
        self._intent_classifier = intent_classifier
        self._retrieval = retrieval
        if self._intent_classifier is None and settings is not None:
            self._intent_classifier = IntentClassifier(
                settings=settings,
                provider=cast(Provider, settings.llm_small_provider),
                model=settings.llm_intent_model,
                timeout=settings.llm_intent_timeout_seconds,
                max_tokens=settings.llm_intent_max_tokens,
            )

    async def handle_session_start(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        session_metadata: dict,
    ) -> SessionStartResult:
        person = await self._fetch_person(person_id)
        # Real Phase Gate / Response Generator land in steps 7-8. For
        # now, name the deceased and ask a generic opener — the shape
        # the real opener will satisfy (CLAUDE.md s6, ARCHITECTURE.md
        # s3.7) but with placeholder content.
        opener = f"Tell me about {person.name}."
        return SessionStartResult(
            opener=opener,
            phase=person.phase,
            selected_question_id=None,
        )

    async def handle_turn(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        user_message: str,
    ) -> TurnResult:
        intent: str | None = None
        emotional_temperature: str | None = None

        if self._intent_classifier is not None:
            try:
                recent_turns = await self._wm.get_transcript(str(session_id))
                state = await self._wm.get_state(str(session_id))
                signals = state.model_dump()
                result = await self._intent_classifier.classify(
                    recent_turns=recent_turns,
                    signals=signals,
                )
                await self._wm.update_signals(
                    str(session_id),
                    signal_last_intent=result.intent,
                    signal_emotional_temperature_estimate=(
                        result.emotional_temperature
                    ),
                )
                intent = result.intent
                emotional_temperature = result.emotional_temperature
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

        n_moments = 0
        n_entities = 0
        n_threads = 0
        if self._retrieval is not None and intent in {"recall", "clarify", "switch"}:
            try:
                moments = await self._retrieval.search_moments(
                    query=user_message,
                    person_id=person_id,
                )
                n_moments = len(moments)
                if intent == "switch":
                    entities = await self._retrieval.get_entities(person_id)
                    threads = await self._retrieval.get_threads(person_id)
                    n_entities = len(entities)
                    n_threads = len(threads)
                log.info(
                    "retrieval_called",
                    session_id=str(session_id),
                    intent=intent,
                    person_id=str(person_id),
                    n_moments=n_moments,
                    n_entities=n_entities,
                    n_threads=n_threads,
                )
            except Exception as e:
                log.warning(
                    "retrieval.failed",
                    session_id=str(session_id),
                    intent=intent,
                    person_id=str(person_id),
                    error=str(e),
                    exc_info=True,
                )
                log.info(
                    "retrieval_called",
                    session_id=str(session_id),
                    intent=intent,
                    person_id=str(person_id),
                    n_moments=0,
                    n_entities=0,
                    n_threads=0,
                )

        # Retrieval, Response Generator, and Segment Detector still land
        # context is intentionally discarded until the Response Generator
        # lands in step 7. For now, return the neutral acknowledgement.
        return TurnResult(
            reply="I hear you. Tell me more.",
            intent=intent,
            emotional_temperature=emotional_temperature,
            segment_boundary=False,
        )

    async def handle_session_wrap(
        self,
        session_id: UUID,
        person_id: UUID,
    ) -> SessionWrapResult:
        # Step-18 replaces this with: force-close segment -> generate
        # session summary -> fan out to background workers.
        return SessionWrapResult(
            session_summary="",
            moments_extracted_estimate=0,
        )

    # --- DB helper ---------------------------------------------------------

    async def _fetch_person(self, person_id: UUID) -> "_Person":
        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT name, phase FROM persons WHERE id = %s",
                    (str(person_id),),
                )
                row = await cur.fetchone()
        if row is None:
            raise PersonNotFoundError(f"person {person_id} not found")
        name, phase = row
        return _Person(name=name, phase=phase)


@dataclass(frozen=True)
class _Person:
    name: str
    phase: str
