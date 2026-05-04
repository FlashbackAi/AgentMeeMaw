from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from flashback.orchestrator.state import SessionStartState, TurnState


def test_turn_state_defaults_are_empty_and_mutable():
    state = TurnState(
        turn_id=uuid4(),
        session_id=uuid4(),
        person_id=uuid4(),
        role_id=uuid4(),
        user_message="hello",
        started_at=datetime.now(timezone.utc),
    )

    assert state.intent_result is None
    assert state.related_moments == []
    assert state.related_entities == []
    assert state.related_threads == []
    assert state.selection is None
    assert state.response is None
    assert state.failures == {}

    state.failures["intent_classify"] = "LLMError: failed"
    assert "intent_classify" in state.failures


def test_session_start_state_defaults_are_empty_and_mutable():
    state = SessionStartState(
        session_id=uuid4(),
        person_id=uuid4(),
        role_id=uuid4(),
        session_metadata={},
        started_at=datetime.now(timezone.utc),
    )

    assert state.person_name == ""
    assert state.person_relationship is None
    assert state.selection is None
    assert state.response is None
    assert state.failures == {}

    state.person_name = "Maya"
    assert state.person_name == "Maya"
