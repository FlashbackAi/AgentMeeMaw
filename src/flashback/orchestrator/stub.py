"""Compatibility imports for the former step-4 stub module."""

from flashback.orchestrator.orchestrator import (
    Orchestrator as StubOrchestrator,
    PersonNotFoundError,
    StarterQuestionNotFoundError,
)
from flashback.orchestrator.protocol import (
    OrchestratorProtocol as Orchestrator,
    SessionStartResult,
    SessionWrapResult,
    TurnResult,
)

__all__ = [
    "Orchestrator",
    "PersonNotFoundError",
    "SessionStartResult",
    "SessionWrapResult",
    "StarterQuestionNotFoundError",
    "StubOrchestrator",
    "TurnResult",
]
