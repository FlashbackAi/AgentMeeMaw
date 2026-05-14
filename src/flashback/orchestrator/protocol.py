"""Protocol and result shapes consumed by the HTTP layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Tap(BaseModel):
    """A tappable question chip surfaced beneath an agent reply.

    `options` are short tappable answer chips generated per-turn by a
    small LLM call. Empty list when generation failed or was skipped —
    the UI falls back to free-text input only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: UUID
    text: str
    dimension: str
    options: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SessionStartResult:
    opener: str
    phase: str
    selected_question_id: UUID | None
    taps: list[Tap]


@dataclass(frozen=True)
class TurnResult:
    reply: str
    intent: str | None
    emotional_temperature: str | None
    segment_boundary: bool
    taps: list[Tap]


@dataclass(frozen=True)
class SessionWrapResult:
    session_summary: str
    segments_extracted_count: int


class OrchestratorProtocol(Protocol):
    """The interface the HTTP routes consume."""

    async def handle_session_start(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        session_metadata: dict,
    ) -> SessionStartResult: ...

    async def handle_first_time_opener(
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
