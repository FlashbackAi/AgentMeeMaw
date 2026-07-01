"""Schemas for identity merge review APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


MergeStatus = Literal["pending", "approved", "rejected", "auto_merged", "unmerged"]


class IdentityMergeSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    person_id: UUID
    source_entity_id: UUID
    source_entity_name: str
    source_entity_description: str | None = None
    target_entity_id: UUID
    target_entity_name: str
    target_entity_description: str | None = None
    proposed_alias: str | None = None
    reason: str
    source: str
    status: MergeStatus
    created_at: datetime
    # SP6b: cross-contributor context (resolved live from collaborator_onboarding).
    cross_contributor: bool = False
    source_told_by_display_name: str | None = None
    target_told_by_display_name: str | None = None


class IdentityMergeActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion_id: UUID
    person_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    status: Literal["approved", "rejected"]


class AutoMergeNotification(BaseModel):
    """One unacknowledged auto-merge for the user-facing toast feed."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    person_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    survivor_name: str
    notification_text: str
    confidence: str | None = None
    acknowledged: bool
    auto_merged_at: datetime | None = None
    # SP6b: cross-contributor context (resolved live from collaborator_onboarding).
    cross_contributor: bool = False
    source_told_by_display_name: str | None = None
    target_told_by_display_name: str | None = None


class UnmergeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion_id: UUID
    person_id: UUID
    survivor_entity_id: UUID
    resurrected_entity_id: UUID
    status: Literal["unmerged"]


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
    auto_merged_count: int = 0
    suggestion_ids: list[UUID]
