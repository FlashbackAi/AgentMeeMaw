"""Pydantic request and response models for the HTTP surface.

Mirrors the contract in CLAUDE.md s8 and the step-4 prompt's API
section. Uses pydantic v2 syntax (``model_config = ConfigDict(...)``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flashback.orchestrator.protocol import Tap


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
    taps: list[Tap] = Field(default_factory=list)


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
    taps: list[Tap] = Field(default_factory=list)


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


# --- /persons --------------------------------------------------------------


class PersonCreateRequest(BaseModel):
    """Body for ``POST /persons``.

    Node calls this once during onboarding, after the contributor has
    supplied the subject's display name, their own relationship to
    them, and their contributor display name. DOB / DOD are deliberately
    not accepted (CLAUDE.md s1).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    relationship: str = Field(min_length=1, max_length=80)
    contributor_display_name: str = Field(min_length=1, max_length=64)
    gender: Literal["he", "she", "they"] | None = None

    @field_validator(
        "name",
        "relationship",
        "contributor_display_name",
        mode="before",
    )
    @classmethod
    def _strip(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class PersonCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    name: str
    relationship: str
    gender: Literal["he", "she", "they"] | None = None
    phase: Literal["starter", "steady"]
    created_at: datetime


# --- /api/v1/onboarding ----------------------------------------------------


class ArchetypeAnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=120)
    option_id: str | None = Field(default=None, max_length=120)
    free_text: str | None = Field(default=None, max_length=500)
    skipped: bool = False

    @field_validator("question_id", "option_id", "free_text", mode="before")
    @classmethod
    def _strip_optional(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class ArchetypeAnswersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    answers: list[ArchetypeAnswerInput] = Field(min_length=3, max_length=5)
    contributor_display_name: str | None = Field(default=None, max_length=64)

    @field_validator("contributor_display_name", mode="before")
    @classmethod
    def _strip_contributor_name(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class ArchetypeAnswersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    opener: str


class ArchetypeQuestionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    relationship: str | None = None
    archetype: str
    questions: list[dict[str, Any]]


# --- /health ---------------------------------------------------------------


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    checks: dict[str, str] = Field(default_factory=dict)
