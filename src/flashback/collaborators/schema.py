"""Pydantic results for collaborator removal / restore (SP6a)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class RemovalResult(BaseModel):
    person_id: UUID
    user_id: UUID
    moments_removed: int
    entities_removed: int
    moments_resurrected: int


class RestoreResult(BaseModel):
    person_id: UUID
    user_id: UUID
    moments_restored: int
    entities_restored: int
    moments_re_superseded: int
