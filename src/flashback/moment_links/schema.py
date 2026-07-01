"""Pydantic models for SP5 same-event links + contradiction review items."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SameEventLink(BaseModel):
    id: UUID
    person_id: UUID
    moment_a_id: UUID
    moment_b_id: UUID
    reason: str | None = None
    status: str
    acknowledged_at: datetime | None = None
    created_at: datetime
    # Live-resolved via JOIN to moments at read time (spec D5).
    moment_a_title: str = ""
    moment_b_title: str = ""
    told_by_a_user_id: UUID | None = None
    told_by_a_display_name: str | None = None
    told_by_b_user_id: UUID | None = None
    told_by_b_display_name: str | None = None


class ContradictionItem(BaseModel):
    id: UUID
    person_id: UUID
    moment_a_id: UUID
    moment_b_id: UUID
    reason: str | None = None
    status: str
    created_at: datetime
    resolved_at: datetime | None = None
    moment_a_title: str = ""
    moment_b_title: str = ""
    told_by_a_user_id: UUID | None = None
    told_by_a_display_name: str | None = None
    told_by_b_user_id: UUID | None = None
    told_by_b_display_name: str | None = None
