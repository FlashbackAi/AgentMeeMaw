"""Compatibility imports for the former step-4 stub module."""

from flashback.orchestrator.errors import (
    PersonNotFoundError,
    StarterQuestionNotFoundError,
)
from flashback.orchestrator.orchestrator import Orchestrator as StubOrchestrator
from flashback.orchestrator.protocol import (
    OrchestratorProtocol as Orchestrator,
    SessionStartResult,
    SessionWrapResult,
    Tap,
    TurnResult,
)

__all__ = [
    "Orchestrator",
    "PersonNotFoundError",
    "SessionStartResult",
    "SessionWrapResult",
    "StarterQuestionNotFoundError",
    "StubOrchestrator",
    "Tap",
    "TurnResult",
]
