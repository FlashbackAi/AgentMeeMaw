from __future__ import annotations

from uuid import uuid4

import pytest

from flashback.orchestrator import orchestrator as orchestrator_module
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.errors import WorkingMemoryNotFound
from flashback.orchestrator.orchestrator import Orchestrator


class FakeWorkingMemory:
    def __init__(self, exists: bool = True) -> None:
        self._exists = exists

    async def exists(self, session_id: str) -> bool:
        return self._exists


def _orch(wm: FakeWorkingMemory) -> Orchestrator:
    return Orchestrator(
        OrchestratorDeps(
            db_pool=None,
            working_memory=wm,
            intent_classifier=None,
            retrieval=None,
            phase_gate=None,
            response_generator=None,
        )
    )


async def test_handle_session_wrap_returns_summary_and_segment_count(monkeypatch):
    async def fake_wrap_session(state, deps):
        state.session_summary_text = "the lake cabin"
        state.segments_pushed_count = 2

    monkeypatch.setattr(orchestrator_module, "wrap_session", fake_wrap_session)

    result = await _orch(FakeWorkingMemory()).handle_session_wrap(
        session_id=uuid4(),
        person_id=uuid4(),
    )

    assert result.session_summary == "the lake cabin"
    assert result.segments_extracted_count == 2


async def test_handle_session_wrap_missing_wm_raises_409_domain_error():
    with pytest.raises(WorkingMemoryNotFound):
        await _orch(FakeWorkingMemory(exists=False)).handle_session_wrap(
            session_id=uuid4(),
            person_id=uuid4(),
        )
