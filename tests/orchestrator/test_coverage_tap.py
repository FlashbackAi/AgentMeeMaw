from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import fakeredis.aioredis
import pytest_asyncio

from flashback.intent_classifier.schema import IntentResult
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.state import TurnState
from flashback.orchestrator.steps.select_coverage_tap import select_coverage_tap
from flashback.working_memory import WorkingMemory

QUESTION_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class FakePool:
    def __init__(
        self,
        *,
        coverage: dict[str, int] | None = None,
        question_id: UUID | None = QUESTION_ID,
    ) -> None:
        self.coverage = coverage or {
            "sensory": 0,
            "voice": 0,
            "place": 0,
            "relation": 0,
            "era": 0,
        }
        self.question_id = question_id

    def connection(self):
        return _AsyncContext(FakeConnection(self))


class FakeConnection:
    def __init__(self, pool: FakePool) -> None:
        self.pool = pool

    def cursor(self):
        return _AsyncContext(FakeCursor(self.pool))


class FakeCursor:
    def __init__(self, pool: FakePool) -> None:
        self.pool = pool
        self.sql = ""

    async def execute(self, sql, params=None):
        self.sql = sql

    async def fetchone(self):
        if "SELECT coverage_state" in self.sql:
            return (self.pool.coverage,)
        if "SELECT name, gender" in self.sql:
            return ("Maya", "female")
        if "FROM active_questions" in self.sql and self.pool.question_id:
            return (self.pool.question_id, "What kind of work did {name} do?")
        return None


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest_asyncio.fixture
async def wm():
    redis = fakeredis.aioredis.FakeRedis()
    memory = WorkingMemory(redis, ttl_seconds=100, transcript_limit=30)
    try:
        yield memory
    finally:
        await redis.aclose()


def _intent(name: str) -> IntentResult:
    return IntentResult(
        intent=name,
        confidence="high",
        emotional_temperature="medium",
        reasoning="test",
    )


async def _state(wm: WorkingMemory, *, intent: str = "switch") -> TurnState:
    session_id = uuid4()
    person_id = uuid4()
    role_id = uuid4()
    now = datetime.now(timezone.utc)
    await wm.initialize(str(session_id), str(person_id), str(role_id), now)
    await wm.append_turn(str(session_id), "assistant", "opener", now)
    await wm.append_turn(str(session_id), "user", "first answer", now)
    await wm.append_turn(str(session_id), "assistant", "reply", now)
    await wm.append_turn(str(session_id), "user", "switch please", now)
    return TurnState(
        turn_id=uuid4(),
        session_id=session_id,
        person_id=person_id,
        role_id=role_id,
        user_message="switch please",
        started_at=now,
        intent_result=_intent(intent),
        effective_intent=intent,
    )


def _deps(wm: WorkingMemory, pool: FakePool) -> OrchestratorDeps:
    return OrchestratorDeps(
        db_pool=pool,
        working_memory=wm,
        intent_classifier=None,
        retrieval=None,
        phase_gate=None,
        response_generator=None,
    )


async def test_tap_fires_on_switch_intent_dim_zero(wm):
    state = await _state(wm, intent="switch")

    await select_coverage_tap(state, _deps(wm, FakePool()))

    assert len(state.taps) == 1
    assert state.taps[0].question_id == QUESTION_ID
    assert state.taps[0].dimension == "era"
    assert "Maya" in state.taps[0].text


async def test_tap_fires_on_clarify_intent(wm):
    state = await _state(wm, intent="clarify")

    await select_coverage_tap(state, _deps(wm, FakePool()))

    assert len(state.taps) == 1


async def test_no_tap_on_recall_deepen_story(wm):
    for intent in ("recall", "deepen", "story"):
        state = await _state(wm, intent=intent)

        await select_coverage_tap(state, _deps(wm, FakePool()))

        assert state.taps == []


async def test_no_tap_when_all_dimensions_have_coverage(wm):
    state = await _state(wm)
    pool = FakePool(
        coverage={
            "sensory": 1,
            "voice": 1,
            "place": 1,
            "relation": 1,
            "era": 1,
        }
    )

    await select_coverage_tap(state, _deps(wm, pool))

    assert state.taps == []


async def test_tap_cap_blocks_third_emission(wm):
    state = await _state(wm)
    await wm.record_tap_emitted(str(state.session_id), str(uuid4()))
    await wm.record_tap_emitted(str(state.session_id), str(uuid4()))

    await select_coverage_tap(state, _deps(wm, FakePool()))

    assert state.taps == []


async def test_bank_exhaustion_returns_empty_list(wm):
    state = await _state(wm)

    await select_coverage_tap(state, _deps(wm, FakePool(question_id=None)))

    assert state.taps == []


async def test_no_tap_on_first_user_turn(wm):
    session_id = uuid4()
    person_id = uuid4()
    role_id = uuid4()
    now = datetime.now(timezone.utc)
    await wm.initialize(str(session_id), str(person_id), str(role_id), now)
    await wm.append_turn(str(session_id), "assistant", "opener", now)
    await wm.append_turn(str(session_id), "user", "switch please", now)
    state = TurnState(
        turn_id=uuid4(),
        session_id=session_id,
        person_id=person_id,
        role_id=role_id,
        user_message="switch please",
        started_at=now,
        intent_result=_intent("switch"),
        effective_intent="switch",
    )

    await select_coverage_tap(state, _deps(wm, FakePool()))

    assert state.taps == []


async def test_wm_counter_increments(wm):
    state = await _state(wm)

    await select_coverage_tap(state, _deps(wm, FakePool()))

    wm_state = await wm.get_state(str(state.session_id))
    assert wm_state.taps_emitted_this_session == 1
    assert wm_state.emitted_tap_question_ids == [str(QUESTION_ID)]
