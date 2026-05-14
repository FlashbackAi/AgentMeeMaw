"""Pydantic result models returned by the Retrieval Service."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class MomentResult(BaseModel):
    id: UUID
    person_id: UUID
    title: str
    narrative: str
    time_anchor: dict | None
    life_period_estimate: str | None
    sensory_details: str | None
    emotional_tone: str | None
    contributor_perspective: str | None
    created_at: datetime
    similarity_score: float | None = None


class EntityResult(BaseModel):
    id: UUID
    person_id: UUID
    kind: Literal["person", "place", "object", "organization"]
    name: str
    description: str | None
    aliases: list[str]
    attributes: dict
    created_at: datetime
    similarity_score: float | None = None


class ThreadResult(BaseModel):
    id: UUID
    person_id: UUID
    name: str
    description: str
    source: Literal["auto-detected", "manual"]
    confidence: float | None
    created_at: datetime


class DroppedPhraseResult(BaseModel):
    question_id: UUID
    text: str
    dropped_phrase: str
    created_at: datetime


class SessionSummaryResult(BaseModel):
    session_id: UUID
    summary: str
    created_at: datetime
