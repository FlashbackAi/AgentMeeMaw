"""apply_collaborator_onboarding mirror step (sub-project 3)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from flashback.orchestrator.state import SessionStartState
from flashback.orchestrator.steps.apply_collaborator_onboarding import (
    apply_collaborator_onboarding,
)


class _FakeConn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self._conn


class _Deps:
    def __init__(self, pool):
        self.db_pool = pool


def _state(user_id, meta):
    return SessionStartState(
        session_id=uuid4(),
        person_id=uuid4(),
        user_id=user_id,
        session_metadata=meta,
        started_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_collaborator_upserts_and_sets_voice_anchor():
    conn = _FakeConn()
    state = _state(
        uuid4(),
        {"role": "collaborator", "voice_anchor_text": "his daughter"},
    )
    await apply_collaborator_onboarding(state, _Deps(_FakePool(conn)))
    assert conn.calls, "expected an upsert"
    assert state.session_metadata["contributor_voice_anchor"] == "his daughter"


@pytest.mark.asyncio
async def test_creator_is_noop():
    conn = _FakeConn()
    state = _state(None, {})  # no role, no user_id
    await apply_collaborator_onboarding(state, _Deps(_FakePool(conn)))
    assert conn.calls == []
    assert "contributor_voice_anchor" not in state.session_metadata


@pytest.mark.asyncio
async def test_collaborator_without_anchor_upserts_no_wm_signal():
    conn = _FakeConn()
    state = _state(uuid4(), {"role": "collaborator", "modal_dismissed_at": "2026-06-16T04:06:10Z"})
    await apply_collaborator_onboarding(state, _Deps(_FakePool(conn)))
    assert conn.calls, "expected an upsert even without an anchor"
    assert "contributor_voice_anchor" not in state.session_metadata
