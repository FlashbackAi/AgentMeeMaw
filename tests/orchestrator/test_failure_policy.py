from __future__ import annotations

from types import SimpleNamespace

import pytest

from flashback.llm.errors import LLMError
from flashback.orchestrator.failure_policy import Policy, execute


async def test_degrade_records_failure_and_returns_none():
    state = SimpleNamespace(failures={})

    async def raising():
        raise LLMError("small call failed")

    result = await execute(
        policies={"intent_classify": Policy.DEGRADE},
        step_name="intent_classify",
        fn=raising,
        state=state,
    )

    assert result is None
    assert state.failures == {"intent_classify": "LLMError: small call failed"}


async def test_propagate_reraises_known_exception():
    state = SimpleNamespace(failures={})

    async def raising():
        raise LLMError("big call failed")

    with pytest.raises(LLMError):
        await execute(
            policies={"generate_response": Policy.PROPAGATE},
            step_name="generate_response",
            fn=raising,
            state=state,
        )
    assert state.failures == {}


async def test_success_returns_value_without_recording_failure():
    state = SimpleNamespace(failures={})

    async def ok():
        return "done"

    result = await execute(
        policies={"intent_classify": Policy.DEGRADE},
        step_name="intent_classify",
        fn=ok,
        state=state,
    )

    assert result == "done"
    assert state.failures == {}


async def test_unknown_step_defaults_to_propagate():
    state = SimpleNamespace(failures={})

    async def raising():
        raise LLMError("unknown")

    with pytest.raises(LLMError):
        await execute(
            policies={},
            step_name="unknown",
            fn=raising,
            state=state,
        )
    assert state.failures == {}


async def test_unexpected_exception_always_propagates():
    state = SimpleNamespace(failures={})

    async def raising():
        raise ValueError("programming bug")

    with pytest.raises(ValueError):
        await execute(
            policies={"intent_classify": Policy.DEGRADE},
            step_name="intent_classify",
            fn=raising,
            state=state,
        )
    assert state.failures == {}
