"""Test that build_turn_context propagates state.user_id -> TurnContext.current_user_id."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


class _FakeWorkingMemory:
    """Stub working memory that returns safe defaults for any WM call.

    ``get_state`` is not expected to be hit (wm_state is pre-set on
    TurnState), but ``get_transcript`` will be called when
    ``state.transcript`` is the empty list (falsy), so we return ``[]``
    harmlessly.
    """

    async def get_state(self, session_id):
        raise AssertionError("get_state should not be called when wm_state is pre-set")

    async def get_transcript(self, session_id):
        return []


@pytest.mark.asyncio
async def test_build_turn_context_propagates_user_id(monkeypatch):
    """build_turn_context must forward state.user_id to TurnContext.current_user_id."""
    from flashback.orchestrator.steps.generate_response import build_turn_context
    from flashback.orchestrator.state import TurnState
    from flashback.working_memory.schema import WorkingMemoryState

    known_user_id = uuid4()
    person_id = uuid4()

    # Pre-built WorkingMemoryState so the function skips the DB call for WM.
    wm_state = WorkingMemoryState(
        person_id=str(person_id),
        started_at=datetime.now(timezone.utc),
    )

    state = TurnState(
        turn_id=uuid4(),
        session_id=uuid4(),
        person_id=person_id,
        user_message="hello",
        started_at=datetime.now(timezone.utc),
        user_id=known_user_id,
        working_memory_state=wm_state,
        transcript=[],
    )

    # Fake person object matching what build_turn_context reads from fetch_person.
    fake_person = SimpleNamespace(
        name="Maya",
        relationship="mother",
        phase="starter",
        gender="she",
    )

    # Monkeypatch fetch_person in the module it is imported from inside
    # build_turn_context: flashback.orchestrator.steps.starter_opener.
    import flashback.orchestrator.steps.starter_opener as _starter_opener_mod

    async def _fake_fetch_person(deps, person_id):
        return fake_person

    monkeypatch.setattr(_starter_opener_mod, "fetch_person", _fake_fetch_person)

    # Minimal fake deps. WM get_state is not called (wm_state pre-set);
    # get_transcript may be called because an empty list is falsy.
    fake_deps = SimpleNamespace(working_memory=_FakeWorkingMemory())

    ctx = await build_turn_context(state, fake_deps)

    assert ctx.current_user_id == known_user_id
