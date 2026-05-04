"""Typed models for Question Producers P2/P3/P5."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ProducerTag = Literal["P2", "P3", "P5"]
SourceTag = Literal[
    "underdeveloped_entity",
    "life_period_gap",
    "universal_dimension",
]


class ProducerMessage(BaseModel):
    """Inbound SQS message body for both producer queues."""

    model_config = ConfigDict(extra="ignore")

    person_id: UUID
    producer: ProducerTag


class GeneratedQuestion(BaseModel):
    """A single question produced by P2, P3, or P5."""

    model_config = ConfigDict(extra="forbid")

    text: str
    themes: list[str] = Field(min_length=1)
    attributes: dict = Field(default_factory=dict)
    targets_entity_id: UUID | None = None


class ProducerResult(BaseModel):
    """Normalized producer output that persistence can write uniformly."""

    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    source_tag: SourceTag
    questions: list[GeneratedQuestion]
    overall_reasoning: str


@dataclass(frozen=True)
class ProducerLLMConfig:
    provider: str
    model: str
    timeout: float
    max_tokens: int

