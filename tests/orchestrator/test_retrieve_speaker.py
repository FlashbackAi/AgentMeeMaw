"""retrieve() forwards the current speaker's user_id to search_moments (SP2)."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from flashback.orchestrator.state import TurnState
from flashback.orchestrator.steps.retrieve import retrieve


class _FakeRetrieval:
    def __init__(self):
        self.search_moments_kwargs = None

    async def search_moments(self, **kwargs):
        self.search_moments_kwargs = kwargs
        return []

    async def search_entities(self, **kwargs):
        return []


class _Deps:
    def __init__(self, retrieval):
        self.retrieval = retrieval


def _state(user_id):
    s = TurnState(
        turn_id=uuid4(),
        session_id=uuid4(),
        person_id=uuid4(),
        user_message="tell me about the halwa",
        started_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        user_id=user_id,
    )
    s.effective_intent = "recall"
    return s


@pytest.mark.asyncio
async def test_recall_forwards_current_user_id():
    retr = _FakeRetrieval()
    user_id = uuid4()
    state = _state(user_id)
    await retrieve(state, _Deps(retr))
    assert retr.search_moments_kwargs is not None
    assert retr.search_moments_kwargs.get("current_user_id") == user_id


@pytest.mark.asyncio
async def test_recall_forwards_none_when_no_user():
    retr = _FakeRetrieval()
    state = _state(None)
    await retrieve(state, _Deps(retr))
    assert retr.search_moments_kwargs.get("current_user_id") is None
