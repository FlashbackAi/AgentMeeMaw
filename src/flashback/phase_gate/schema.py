"""Typed Phase Gate selection outputs."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

Phase = Literal["starter", "steady"]
Dimension = Literal["sensory", "voice", "place", "relation", "era"]


class PhaseGateError(RuntimeError):
    """Raised when deterministic Phase Gate selection cannot proceed."""


class SelectionResult(BaseModel):
    """The output of Phase Gate selection.

    ``question_id`` and ``question_text`` may be ``None`` when the bank is
    empty for a steady-phase legacy. The orchestrator handles ``None`` by
    passing ``seeded_question_text=None`` to the Response Generator.
    """

    model_config = ConfigDict(extra="forbid")

    phase: Phase
    question_id: UUID | None = None
    question_text: str | None = None
    source: str | None = None
    dimension: Dimension | None = None
    rationale: str = ""
