"""SP5: the recall retrieval step populates linked_account_moments."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from flashback.orchestrator.steps.retrieve import retrieve
from flashback.retrieval.schema import MomentResult


class _FakeRetrieval:
    def __init__(self, moments, linked):
        self._moments = moments
        self._linked = linked
        self.linked_calls = []

    async def search_moments(self, *, query, person_id, current_user_id):
        return self._moments

    async def search_entities(self, *, query, person_id):
        return []

    async def get_same_event_linked_moments(self, person_id, moment_ids):
        self.linked_calls.append(list(moment_ids))
        return self._linked


def _m(title):
    return MomentResult(
        id=uuid4(), person_id=uuid4(), title=title, narrative="n",
        time_anchor=None, life_period_estimate=None, sensory_details=None,
        emotional_tone=None, contributor_perspective=None,
        created_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
    )


def _state():
    return SimpleNamespace(
        effective_intent="recall", user_message="q",
        person_id=uuid4(), user_id=uuid4(),
        related_moments=[], related_entities=[], related_threads=[],
        linked_account_moments=[],
    )


@pytest.mark.asyncio
async def test_recall_populates_linked_account_moments():
    base = _m("base")
    linked = _m("linked")
    deps = SimpleNamespace(retrieval=_FakeRetrieval([base], [linked]))
    state = _state()
    await retrieve(state, deps)
    assert [m.title for m in state.linked_account_moments] == ["linked"]
    assert deps.retrieval.linked_calls == [[base.id]]


@pytest.mark.asyncio
async def test_no_related_moments_skips_linked_lookup():
    deps = SimpleNamespace(retrieval=_FakeRetrieval([], []))
    state = _state()
    await retrieve(state, deps)
    assert state.linked_account_moments == []
    assert deps.retrieval.linked_calls == []
