"""Pydantic shapes for Session Summary generation."""

from __future__ import annotations

from pydantic import BaseModel


class SessionSummaryContext(BaseModel):
    person_name: str
    relationship: str | None
    rolling_summary: str


class SessionSummaryResult(BaseModel):
    text: str
