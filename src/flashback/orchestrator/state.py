"""Mutable state objects passed between orchestrator steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from flashback.intent_classifier.schema import Intent, IntentResult, Temperature
from flashback.phase_gate.schema import SelectionResult
from flashback.response_generator.schema import ResponseResult
from flashback.retrieval.schema import EntityResult, MomentResult, ThreadResult
from flashback.working_memory.schema import Turn, WorkingMemoryState


@dataclass
class TurnState:
    """Mutable state passed between turn-loop steps.

    Each step reads what it needs and writes its output. ``failures``
    records graceful-degradation events so the final log line can surface
    the degraded steps.
    """

    turn_id: UUID
    session_id: UUID
    person_id: UUID
    role_id: UUID
    user_message: str
    started_at: datetime

    transcript: list[Turn] = field(default_factory=list)
    working_memory_state: WorkingMemoryState | None = None
    person_name: str = ""
    person_relationship: str | None = None
    person_phase: str = ""

    intent_result: IntentResult | None = None
    effective_intent: Intent = "story"
    effective_temperature: Temperature = "medium"
    related_moments: list[MomentResult] = field(default_factory=list)
    related_entities: list[EntityResult] = field(default_factory=list)
    related_threads: list[ThreadResult] = field(default_factory=list)
    selection: SelectionResult | None = None
    response: ResponseResult | None = None
    segment_boundary_detected: bool = False

    failures: dict[str, str] = field(default_factory=dict)


@dataclass
class SessionStartState:
    """Mutable state for ``handle_session_start``."""

    session_id: UUID
    person_id: UUID
    role_id: UUID
    session_metadata: dict[str, Any]
    started_at: datetime

    person_name: str = ""
    person_relationship: str | None = None
    person_phase: str = ""
    selection: SelectionResult | None = None
    response: ResponseResult | None = None
    failures: dict[str, str] = field(default_factory=dict)
