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
    """Context for the opening assistant message of a returning session.

    First-time openers (immediately after onboarding) use
    :class:`FirstTimeOpenerContext` instead — archetype answers only feed
    that path.
    """

    model_config = ConfigDict(extra="forbid")

    person_name: str
    person_relationship: str | None = None
    person_gender: str = "they"
    contributor_display_name: str | None = None
    contributor_role: str | None = None
    anchor_question_text: str
    anchor_dimension: AnchorDimension | None = None
    prior_session_summary: str | None = None


class FirstTimeOpenerContext(BaseModel):
    """Context for the very first opener, right after archetype onboarding.

    Used once per legacy. After this session, archetype answers have
    already been absorbed into the graph (entities, coverage, embeddings)
    and the normal :class:`StarterContext` path takes over.
    """

    model_config = ConfigDict(extra="forbid")

    person_name: str
    person_relationship: str | None = None
    person_gender: str = "they"
    contributor_display_name: str | None = None
    anchor_question_text: str
    anchor_dimension: AnchorDimension | None = None
    archetype_answers: list[dict] = Field(default_factory=list)


class TurnContext(BaseModel):
    """Context for a regular `/turn` response."""

    model_config = ConfigDict(extra="forbid")

    person_name: str
    person_relationship: str | None = None
    person_gender: str = "they"
    intent: Intent
    emotional_temperature: Temperature
    rolling_summary: str = ""
    prior_session_summary: str = ""
    recent_turns: list[Turn] = Field(default_factory=list)
    related_moments: list[MomentResult] = Field(default_factory=list)
    related_entities: list[EntityResult] = Field(default_factory=list)
    related_threads: list[ThreadResult] = Field(default_factory=list)
    mentioned_entities: list[EntityResult] = Field(default_factory=list)
    ambiguous_mention: bool = False
    seeded_question_text: str | None = None


class ResponseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
