"""Typed inputs and outputs for response generation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from flashback.intent_classifier.schema import Intent, Temperature
from flashback.retrieval.schema import EntityResult, MomentResult, ThreadResult

AnchorDimension = Literal["sensory", "voice", "place", "relation", "era"]


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime


class StarterContext(BaseModel):
    """Context for the first assistant message of a session."""

    model_config = ConfigDict(extra="forbid")

    person_name: str
    person_relationship: str | None = None
    person_gender: str = "they"
    contributor_role: str | None = None
    anchor_question_text: str
    anchor_dimension: AnchorDimension
    prior_session_summary: str | None = None


class TurnContext(BaseModel):
    """Context for a regular `/turn` response."""

    model_config = ConfigDict(extra="forbid")

    person_name: str
    person_relationship: str | None = None
    person_gender: str = "they"
    intent: Intent
    emotional_temperature: Temperature
    rolling_summary: str = ""
    recent_turns: list[Turn] = Field(default_factory=list)
    related_moments: list[MomentResult] = Field(default_factory=list)
    related_entities: list[EntityResult] = Field(default_factory=list)
    related_threads: list[ThreadResult] = Field(default_factory=list)
    seeded_question_text: str | None = None


class ResponseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
