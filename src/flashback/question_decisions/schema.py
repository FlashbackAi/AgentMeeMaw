"""Pydantic models + literal action set for question_decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

Action = Literal["skip", "suppress", "defer"]
ACTIONS: tuple[Action, ...] = ("skip", "suppress", "defer")


class QuestionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    question_id: UUID
    person_id: UUID
    action: Action
    decided_at: datetime
    status: Literal["active", "superseded"]
