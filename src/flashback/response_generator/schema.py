"""Typed inputs and outputs for response generation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from flashback.intent_classifier.schema import Intent, Temperature
from flashback.retrieval.schema import EntityResult, MomentResult, ThreadResult

AnchorDimension = Literal["sensory", "voice", "place", "relation", "era"]
Mode = Literal["text", "voice"]


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
    # Collaborator's relationship to the subject (sub-project 3), e.g.
    # "his daughter". When present, the opener grounds in it. None for the
    # creator / contributors without a captured voice anchor.
    contributor_voice_anchor: str | None = None
    anchor_question_text: str | None = None
    anchor_dimension: AnchorDimension | None = None
    prior_session_summary: str | None = None

    # Theme deepen context (optional). When set, the opener should
    # acknowledge that this is a theme-focused session — without
    # turning into a survey.
    current_theme_display_name: str | None = None
    current_theme_kind: str | None = None  # 'universal' | 'emergent'
    theme_archetype_answers: list[dict] = Field(default_factory=list)

    mode: Mode = "text"


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
    anchor_question_text: str | None = None
    anchor_dimension: AnchorDimension | None = None
    archetype_answers: list[dict] = Field(default_factory=list)
    mode: Mode = "text"


class TurnContext(BaseModel):
    """Context for a regular `/turn` response."""

    model_config = ConfigDict(extra="forbid")

    person_name: str
    person_relationship: str | None = None
    person_gender: str = "they"
    # Current speaker (SP2). render_turn_context uses this to decide which
    # retrieved moments belong to OTHER contributors and must be credited.
    # None = unknown/single-contributor -> no attribution.
    current_user_id: UUID | None = None
    intent: Intent
    emotional_temperature: Temperature
    rolling_summary: str = ""
    prior_session_summary: str = ""
    recent_turns: list[Turn] = Field(default_factory=list)
    related_moments: list[MomentResult] = Field(default_factory=list)
    # SP5 (#28): active same-event-linked moments for the retrieved set, on
    # recall only. Rendered in <linked_accounts>; cross-contributor attribution
    # reuses the same guard as <moments>.
    linked_account_moments: list[MomentResult] = Field(default_factory=list)
    related_entities: list[EntityResult] = Field(default_factory=list)
    related_threads: list[ThreadResult] = Field(default_factory=list)
    mentioned_entities: list[EntityResult] = Field(default_factory=list)
    ambiguous_mention: bool = False
    seeded_question_text: str | None = None
    tap_pending: bool = False
    tap_question_text: str | None = None
    # Free-form: usually one of AnchorDimension but may be empty / non-anchor
    # when a steady-selector seeded question is promoted to a tap.
    tap_dimension: str | None = None

    # Active deepen-session theme, if any. Soft bias: the agent should
    # tilt toward this theme but follow the user when conversation drifts.
    current_theme_display_name: str | None = None

    mode: Mode = "text"


class ResponseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    # Voice-mode prosody label lifted from the reply's leading [[style: x]]
    # tag (None in text mode). Node maps it to a Gemini TTS style.
    voice_style: str | None = None
