from __future__ import annotations

from uuid import UUID

import pytest

from flashback.phase_gate.schema import PhaseGateError
from flashback.phase_gate.starter_selector import StarterSelector

PERSON_ID = UUID("11111111-1111-1111-1111-111111111111")
QUESTION_ID = UUID("33333333-3333-3333-3333-333333333333")


class FakeStarterPool:
    def __init__(
        self,
        *,
        has_moments: bool,
        coverage_state: dict,
        unanswered: dict[str, list[tuple[UUID, str]]] | None = None,
        any_templates: dict[str, list[tuple[UUID, str]]] | None = None,
    ) -> None:
        self.has_moments = has_moments
        self.coverage_state = coverage_state
        self.unanswered = unanswered or {}
        self.any_templates = any_templates or {}
        self.last_dimension: str | None = None

    def connection(self):
        return _AsyncContext(FakeConnection(self))


class FakeConnection:
    def __init__(self, pool: FakeStarterPool) -> None:
        self.pool = pool

    def cursor(self):
        return _AsyncContext(FakeCursor(self.pool))


class FakeCursor:
    def __init__(self, pool: FakeStarterPool) -> None:
        self.pool = pool
        self.sql = ""
        self.params = {}

    async def execute(self, sql, params=None):
        self.sql = sql
        self.params = params or {}
        if "dimension" in self.params:
            self.pool.last_dimension = self.params["dimension"]

    async def fetchone(self):
        if "FROM active_moments" in self.sql:
            return (self.pool.has_moments,)
        if "SELECT coverage_state" in self.sql:
            return (self.pool.coverage_state,)
        if "FROM active_questions" in self.sql:
            dimension = self.params["dimension"]
            source = (
                self.pool.unanswered
                if "NOT EXISTS" in self.sql
                else self.pool.any_templates
            )
            rows = source.get(dimension, [])
            return rows[0] if rows else None
        raise AssertionError(f"unexpected SQL: {self.sql}")


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _template(text: str = "Question?") -> tuple[UUID, str]:
    return (QUESTION_ID, text)


async def test_first_turn_ever_forces_sensory():
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={"sensory": 9, "voice": 0, "place": 0, "relation": 0, "era": 0},
        unanswered={"sensory": [_template()]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.dimension == "sensory"
    assert pool.last_dimension == "sensory"


async def test_all_zero_coverage_tiebreaks_to_sensory():
    pool = FakeStarterPool(
        has_moments=True,
        coverage_state={"sensory": 0, "voice": 0, "place": 0, "relation": 0, "era": 0},
        unanswered={"sensory": [_template()]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.dimension == "sensory"


async def test_sensory_covered_picks_voice():
    pool = FakeStarterPool(
        has_moments=True,
        coverage_state={"sensory": 1, "voice": 0, "place": 0, "relation": 0, "era": 0},
        unanswered={"voice": [_template()]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.dimension == "voice"


async def test_tiebreak_among_lowest_picks_place():
    pool = FakeStarterPool(
        has_moments=True,
        coverage_state={"sensory": 2, "voice": 2, "place": 0, "relation": 0, "era": 0},
        unanswered={"place": [_template()]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.dimension == "place"


async def test_answered_filter_uses_unanswered_template():
    chosen = UUID("44444444-4444-4444-4444-444444444444")
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={},
        unanswered={"sensory": [(chosen, "Unanswered")]},
        any_templates={"sensory": [(QUESTION_ID, "Answered"), (chosen, "Unanswered")]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.question_id == chosen
    assert result.question_text == "Unanswered"


async def test_all_templates_answered_falls_back_to_any_template():
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={},
        unanswered={"sensory": []},
        any_templates={"sensory": [_template("Fallback")]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.question_text == "Fallback"
    assert "fallback" in result.rationale


async def test_no_templates_raises_phase_gate_error():
    pool = FakeStarterPool(has_moments=False, coverage_state={})

    with pytest.raises(PhaseGateError):
        await StarterSelector(pool).select(PERSON_ID)
