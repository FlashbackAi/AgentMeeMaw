from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from flashback.phase_gate.steady_selector import SteadySelector

PERSON_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
Q1 = UUID("33333333-3333-3333-3333-333333333333")
Q2 = UUID("44444444-4444-4444-4444-444444444444")
Q3 = UUID("55555555-5555-5555-5555-555555555555")
BASE = datetime(2026, 5, 4, tzinfo=timezone.utc)


class FakeWM:
    def __init__(self, recent_ids: list[str] | None = None) -> None:
        self.recent_ids = recent_ids or []

    async def get_recently_asked_question_ids(self, session_id: str) -> list[str]:
        return self.recent_ids


class FakeSteadyPool:
    def __init__(self, *, candidates=None, recent_themes=None) -> None:
        self.candidates = candidates or []
        self.recent_themes = recent_themes or []

    def connection(self):
        return _AsyncContext(FakeConnection(self))


class FakeConnection:
    def __init__(self, pool: FakeSteadyPool) -> None:
        self.pool = pool

    def cursor(self):
        return _AsyncContext(FakeCursor(self.pool))


class FakeCursor:
    def __init__(self, pool: FakeSteadyPool) -> None:
        self.pool = pool
        self.sql = ""

    async def execute(self, sql, params=None):
        self.sql = sql

    async def fetchone(self):
        if "array_agg" in self.sql:
            return (self.pool.recent_themes,)
        raise AssertionError(f"unexpected SQL: {self.sql}")

    async def fetchall(self):
        if "FROM active_questions" in self.sql:
            return self.pool.candidates
        raise AssertionError(f"unexpected SQL: {self.sql}")


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _row(
    question_id: UUID,
    source: str,
    themes: list[str],
    *,
    created_at: datetime,
    text: str = "Question?",
):
    return (
        question_id,
        text,
        source,
        {"themes": themes},
        created_at,
    )


async def test_empty_bank_returns_none_selection():
    result = await SteadySelector(FakeSteadyPool(), FakeWM()).select(
        PERSON_ID,
        SESSION_ID,
    )

    assert result.phase == "steady"
    assert result.question_id is None
    assert result.question_text is None
    assert result.source is None
    assert result.dimension is None


async def test_single_dropped_reference_selected():
    pool = FakeSteadyPool(
        candidates=[_row(Q1, "dropped_reference", ["porch"], created_at=BASE)]
    )

    result = await SteadySelector(pool, FakeWM()).select(PERSON_ID, SESSION_ID)

    assert result.question_id == Q1
    assert result.source == "dropped_reference"


async def test_source_priority_beats_newer_universal():
    pool = FakeSteadyPool(
        candidates=[
            _row(Q3, "universal_dimension", ["new"], created_at=BASE + timedelta(3)),
            _row(Q1, "dropped_reference", ["old"], created_at=BASE + timedelta(2)),
            _row(Q2, "underdeveloped_entity", ["old"], created_at=BASE),
        ]
    )

    result = await SteadySelector(pool, FakeWM()).select(PERSON_ID, SESSION_ID)

    assert result.question_id == Q1


async def test_diversity_prefers_novel_themes_for_same_source():
    pool = FakeSteadyPool(
        candidates=[
            _row(Q1, "thread_deepen", ["family"], created_at=BASE + timedelta(2)),
            _row(Q2, "thread_deepen", ["work"], created_at=BASE),
        ],
        recent_themes=["family"],
    )
    wm = FakeWM([str(Q3)])

    result = await SteadySelector(pool, wm).select(PERSON_ID, SESSION_ID)

    assert result.question_id == Q2


async def test_universal_dimension_demotion_when_non_universal_is_close():
    pool = FakeSteadyPool(
        candidates=[
            _row(Q1, "universal_dimension", ["novel"], created_at=BASE + timedelta(2)),
            _row(Q2, "underdeveloped_entity", ["family"], created_at=BASE),
        ],
        recent_themes=["family"],
    )
    wm = FakeWM([str(Q3)])

    result = await SteadySelector(pool, wm).select(PERSON_ID, SESSION_ID)

    assert result.question_id == Q2


async def test_universal_dimension_demotion_does_not_fire_when_gap_is_large():
    pool = FakeSteadyPool(
        candidates=[
            _row(Q1, "universal_dimension", ["novel"], created_at=BASE + timedelta(2)),
            _row(Q2, "unknown_non_universal", [], created_at=BASE),
        ],
    )

    result = await SteadySelector(pool, FakeWM()).select(PERSON_ID, SESSION_ID)

    assert result.question_id == Q1


async def test_tiebreaker_newest_wins_identical_scores():
    pool = FakeSteadyPool(
        candidates=[
            _row(Q1, "life_period_gap", ["family"], created_at=BASE),
            _row(Q2, "life_period_gap", ["family"], created_at=BASE + timedelta(2)),
        ]
    )

    result = await SteadySelector(pool, FakeWM()).select(PERSON_ID, SESSION_ID)

    assert result.question_id == Q2
