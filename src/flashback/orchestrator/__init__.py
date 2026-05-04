"""
The Turn Orchestrator surface (CLAUDE.md s3, ARCHITECTURE.md s3.2).

Step 4 ships a stub implementation that returns canned text but
correctly reads from and writes to Working Memory and Postgres. Step 9
replaces :class:`StubOrchestrator` with the real Turn Orchestrator;
the protocol stays the same so the swap is mechanical.
"""

from flashback.orchestrator.stub import (
    Orchestrator,
    SessionStartResult,
    SessionWrapResult,
    StubOrchestrator,
    TurnResult,
)

__all__ = [
    "Orchestrator",
    "SessionStartResult",
    "SessionWrapResult",
    "StubOrchestrator",
    "TurnResult",
]
