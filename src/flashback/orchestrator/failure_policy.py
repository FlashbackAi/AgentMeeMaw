"""Single source of truth for orchestrator failure handling.

Policies:
- DEGRADE: log the failure, record it on ``state.failures``, continue.
- PROPAGATE: let the exception bubble up to the HTTP layer.
"""

from __future__ import annotations

from enum import Enum
from typing import Awaitable, Callable, TypeVar

import structlog

from flashback.llm.errors import LLMError
from flashback.phase_gate.errors import PhaseGateError

log = structlog.get_logger("flashback.orchestrator.failure_policy")
T = TypeVar("T")


class Policy(str, Enum):
    DEGRADE = "degrade"
    PROPAGATE = "propagate"


TURN_POLICIES: dict[str, Policy] = {
    "append_user_turn": Policy.PROPAGATE,
    "intent_classify": Policy.DEGRADE,
    "retrieve": Policy.DEGRADE,
    "select_question": Policy.DEGRADE,
    "generate_response": Policy.PROPAGATE,
    "append_assistant": Policy.PROPAGATE,
}

SESSION_START_POLICIES: dict[str, Policy] = {
    "load_person": Policy.PROPAGATE,
    "select_starter_anchor": Policy.PROPAGATE,
    "generate_opener": Policy.PROPAGATE,
    "init_working_memory": Policy.PROPAGATE,
    "append_opener": Policy.PROPAGATE,
}


async def execute(
    *,
    policies: dict[str, Policy],
    step_name: str,
    fn: Callable[[], Awaitable[T]],
    state: object,
) -> T | None:
    """Run a step under its policy."""

    policy = policies.get(step_name, Policy.PROPAGATE)
    try:
        return await fn()
    except (LLMError, PhaseGateError) as exc:
        if policy == Policy.DEGRADE:
            log.warning(
                "step_degraded",
                step=step_name,
                error=type(exc).__name__,
                detail=str(exc),
            )
            failures = getattr(state, "failures")
            failures[step_name] = f"{type(exc).__name__}: {exc}"
            return None
        raise
