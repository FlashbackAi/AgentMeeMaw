"""Schemas for identity merge review APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


MergeStatus = Literal["pending", "approved", "rejected"]


class IdentityMergeSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    person_id: UUID
    source_entity_id: UUID
    source_entity_name: str
    target_entity_id: UUID
    target_entity_name: str
    proposed_alias: str | None = None
    reason: str
    source: str
    status: MergeStatus
    created_at: datetime


class IdentityMergeActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion_id: UUID
    person_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    status: Literal["approved", "rejected"]


class IdentityMergeScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    limit: int = Field(default=20, ge=1, le=100)


class IdentityMergeScanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    candidates_considered: int
    verifier_calls: int
    suggestions_created: int
    suggestion_ids: list[UUID]
