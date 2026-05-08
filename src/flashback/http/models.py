"""Pydantic request and response models for the HTTP surface.

Mirrors the contract in CLAUDE.md s8 and the step-4 prompt's API
section. Uses pydantic v2 syntax (``model_config = ConfigDict(...)``).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# --- /session/start --------------------------------------------------------


class SessionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    person_id: UUID
    role_id: UUID
    contributor_display_name: str | None = None
    session_metadata: dict = Field(default_factory=dict)


class SessionStartMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["starter", "steady"]
    selected_question_id: UUID | None = None


class SessionStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    opener: str
    metadata: SessionStartMetadata


# --- /turn -----------------------------------------------------------------


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    person_id: UUID
    role_id: UUID
    message: str = Field(min_length=1, max_length=8000)


class TurnMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str | None = None
    emotional_temperature: Literal["low", "medium", "high"] | None = None
    segment_boundary: bool = False


class TurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    metadata: TurnMetadata


# --- /session/wrap ---------------------------------------------------------


class SessionWrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    person_id: UUID


class SessionWrapMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments_extracted_count: int = 0


class SessionWrapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_summary: str
    metadata: SessionWrapMetadata


# --- /admin/reset_phase ----------------------------------------------------


class ResetPhaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID


class ResetPhaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    previous_phase: Literal["starter", "steady"]
    previous_locked_at: str | None = None


# --- /health ---------------------------------------------------------------


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    checks: dict[str, str] = Field(default_factory=dict)
