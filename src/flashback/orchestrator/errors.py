"""Orchestrator-domain exceptions."""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base for orchestrator-internal errors."""


class WorkingMemoryNotFound(OrchestratorError):
    """Raised when an endpoint is called for a session that was not started."""


class PersonNotFound(OrchestratorError, LookupError):
    """Raised when ``/session/start`` references a missing person."""


class StarterQuestionNotFound(OrchestratorError, RuntimeError):
    """Raised when the starter-anchor bank cannot produce a question."""


PersonNotFoundError = PersonNotFound
StarterQuestionNotFoundError = StarterQuestionNotFound
