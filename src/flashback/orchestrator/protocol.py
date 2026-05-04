"""Protocol and result shapes consumed by the HTTP layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class SessionStartResult:
    opener: str
    phase: str
    selected_question_id: UUID | None


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


class OrchestratorProtocol(Protocol):
    """The interface the HTTP routes consume."""

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
