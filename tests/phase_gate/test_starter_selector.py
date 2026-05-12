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
        person_name: str = "Test Subject",
        person_gender: str | None = None,
    ) -> None:
        self.has_moments = has_moments
        self.coverage_state = coverage_state
        self.unanswered = unanswered or {}
        self.any_templates = any_templates or {}
        self.person_name = person_name
        self.person_gender = person_gender
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
        if "SELECT name, gender" in self.sql and "FROM persons" in self.sql:
            return (self.pool.person_name, self.pool.person_gender)
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


async def test_first_turn_ever_forces_era():
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={"sensory": 9, "voice": 0, "place": 0, "relation": 0, "era": 0},
        unanswered={"era": [_template()]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.dimension == "era"
    assert pool.last_dimension == "era"


async def test_all_zero_coverage_tiebreaks_to_era():
    pool = FakeStarterPool(
        has_moments=True,
        coverage_state={"sensory": 0, "voice": 0, "place": 0, "relation": 0, "era": 0},
        unanswered={"era": [_template()]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.dimension == "era"


async def test_era_covered_picks_relation():
    pool = FakeStarterPool(
        has_moments=True,
        coverage_state={"sensory": 0, "voice": 0, "place": 0, "relation": 0, "era": 1},
        unanswered={"relation": [_template()]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.dimension == "relation"


async def test_tiebreak_among_lowest_picks_place():
    pool = FakeStarterPool(
        has_moments=True,
        coverage_state={"sensory": 2, "voice": 2, "place": 0, "relation": 2, "era": 2},
        unanswered={"place": [_template()]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.dimension == "place"


async def test_answered_filter_uses_unanswered_template():
    chosen = UUID("44444444-4444-4444-4444-444444444444")
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={},
        unanswered={"era": [(chosen, "Unanswered")]},
        any_templates={"era": [(QUESTION_ID, "Answered"), (chosen, "Unanswered")]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.question_id == chosen
    assert result.question_text == "Unanswered"


async def test_all_templates_answered_falls_back_to_any_template():
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={},
        unanswered={"era": []},
        any_templates={"era": [_template("Fallback")]},
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.question_text == "Fallback"
    assert "fallback" in result.rationale


async def test_no_templates_raises_phase_gate_error():
    pool = FakeStarterPool(has_moments=False, coverage_state={})

    with pytest.raises(PhaseGateError):
        await StarterSelector(pool).select(PERSON_ID)


async def test_name_placeholder_is_substituted_with_person_name():
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={},
        unanswered={"era": [_template("What did {name} do for work?")]},
        person_name="Margaret",
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.question_text == "What did Margaret do for work?"


async def test_text_without_placeholder_is_returned_unchanged():
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={},
        unanswered={"era": [_template("Plain question.")]},
        person_name="Margaret",
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.question_text == "Plain question."


async def test_female_pronouns_are_substituted():
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={},
        unanswered={
            "era": [_template("What part of {their} life do you know best?")]
        },
        person_name="Margaret",
        person_gender="female",
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.question_text == "What part of her life do you know best?"


async def test_male_pronouns_are_substituted():
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={},
        unanswered={
            "era": [_template("What was home like for {them}?")]
        },
        person_name="George",
        person_gender="male",
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.question_text == "What was home like for him?"


async def test_unknown_gender_defaults_to_they():
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={},
        unanswered={
            "era": [
                _template(
                    "{name} was always doing something with {their} hands; what was {they} like?"
                )
            ]
        },
        person_name="Sam",
        person_gender=None,
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert (
        result.question_text
        == "Sam was always doing something with their hands; what was they like?"
    )


async def test_unrecognized_gender_defaults_to_they():
    pool = FakeStarterPool(
        has_moments=False,
        coverage_state={},
        unanswered={"era": [_template("{their} life mattered.")]},
        person_name="Riley",
        person_gender="nonbinary",
    )

    result = await StarterSelector(pool).select(PERSON_ID)

    assert result.question_text == "their life mattered."
