"""Pydantic / dataclass shapes for profile_facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["starter_extraction", "user_edit"]
Status = Literal["active", "superseded"]


@dataclass(frozen=True)
class ProfileFact:
    """One row from the ``profile_facts`` table."""

    id: UUID
    person_id: UUID
    fact_key: str
    question_text: str
    answer_text: str
    source: Source
    status: Status
    superseded_by: UUID | None
    created_at: datetime
    updated_at: datetime


class ExtractedFact(BaseModel):
    """One candidate fact returned by the extraction LLM tool call.

    The extractor returns a list of these per profile-summary run. The
    runner filters by confidence and applies the per-session limit
    before handing each one to :func:`upsert_fact`.
    """

    model_config = ConfigDict(extra="forbid")

    fact_key: str = Field(min_length=1, max_length=64)
    question_text: str = Field(min_length=1, max_length=300)
    answer_text: str = Field(min_length=1, max_length=300)
    confidence: Literal["low", "medium", "high"]


class FactUpsertRequest(BaseModel):
    """HTTP body for ``POST /profile_facts/upsert``.

    ``question_text`` is optional: if omitted and ``fact_key`` is one of
    the seed slugs, the canonical phrasing from
    :data:`flashback.profile_facts.SEED_FACT_QUESTIONS` is used.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    fact_key: str = Field(min_length=1, max_length=64)
    answer_text: str = Field(min_length=1, max_length=300)
    question_text: str | None = Field(default=None, max_length=300)


class FactUpsertResponse(BaseModel):
    fact_id: UUID
    person_id: UUID
    fact_key: str
    superseded_id: UUID | None
    cap_reached: bool
