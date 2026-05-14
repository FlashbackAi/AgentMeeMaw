from __future__ import annotations

from uuid import UUID

from flashback.phase_gate.gate import PhaseGate
from flashback.phase_gate.schema import SelectionResult

PERSON_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
QUESTION_ID = UUID("33333333-3333-3333-3333-333333333333")


class FakeSelector:
    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.calls = 0

    async def select(self, *args, **kwargs):
        self.calls += 1
        self.sources = kwargs.get("sources")
        return SelectionResult(
            phase=self.phase,
            question_id=QUESTION_ID,
            question_text="Question?",
            source="dropped_reference",
            dimension=None,
            rationale="fake",
        )


class FakePool:
    def __init__(self, phase: str) -> None:
        self.phase = phase

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

    async def execute(self, sql, params=None):
        pass

    async def fetchone(self):
        return (self.pool.phase,)


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_select_next_question_routes_starter_phase():
    steady = FakeSelector("steady")
    gate = PhaseGate(FakePool("starter"), steady)

    result = await gate.select_next_question(PERSON_ID, SESSION_ID)

    assert steady.calls == 1
    assert steady.sources == (
        "underdeveloped_entity",
        "life_period_gap",
        "universal_dimension",
    )
    assert result.phase == "starter"
    assert result.rationale


async def test_select_next_question_routes_steady_phase():
    steady = FakeSelector("steady")
    gate = PhaseGate(FakePool("steady"), steady)

    result = await gate.select_next_question(PERSON_ID, SESSION_ID)

    assert steady.calls == 1
    assert result.phase == "steady"
    assert result.rationale
