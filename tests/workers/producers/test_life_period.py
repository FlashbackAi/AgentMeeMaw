"""P3 life-period gap producer tests."""

from __future__ import annotations

from uuid import UUID

from flashback.workers.producers import life_period as p3_mod
from flashback.workers.producers.life_period import P3LifePeriodGap

from tests.workers.producers.conftest import queued_call_with_tool, seed_moment
from tests.workers.producers.fixtures.sample_states import p3_result


def test_year_based_gaps(db_pool, make_person, stub_settings) -> None:
    person_id = make_person("Years")
    seed_moment(db_pool, person_id=person_id, year=1950)
    seed_moment(db_pool, person_id=person_id, year=1980)

    gaps = P3LifePeriodGap()._find_gaps(db_pool, UUID(person_id), stub_settings)

    assert [g.label for g in gaps] == ["1960s", "1970s"]


def test_all_decades_covered(db_pool, make_person, stub_settings) -> None:
    person_id = make_person("Covered")
    for year in (1950, 1960, 1970):
        seed_moment(db_pool, person_id=person_id, year=year)

    gaps = P3LifePeriodGap()._find_gaps(db_pool, UUID(person_id), stub_settings)

    assert gaps == []


def test_life_period_fallback(db_pool, make_person, stub_settings) -> None:
    person_id = make_person("Periods")
    seed_moment(db_pool, person_id=person_id, life_period_estimate="childhood")
    seed_moment(db_pool, person_id=person_id, life_period_estimate="young adult")

    gaps = P3LifePeriodGap()._find_gaps(db_pool, UUID(person_id), stub_settings)

    assert gaps[0].label == "youth"
    assert all(g.kind == "life_period" for g in gaps)


def test_gap_cap(db_pool, make_person, stub_settings) -> None:
    person_id = make_person("Cap")
    seed_moment(db_pool, person_id=person_id, year=1900)
    seed_moment(db_pool, person_id=person_id, year=1950)
    stub_settings.p3_max_gaps_per_run = 2

    gaps = P3LifePeriodGap()._find_gaps(db_pool, UUID(person_id), stub_settings)

    assert [g.label for g in gaps] == ["1910s", "1920s"]


async def test_llm_happy_path(db_pool, make_person, stub_settings, monkeypatch) -> None:
    person_id = make_person("P3 llm")
    seed_moment(db_pool, person_id=person_id, year=1950)
    seed_moment(db_pool, person_id=person_id, year=1970)
    monkeypatch.setattr(
        p3_mod,
        "call_with_tool",
        queued_call_with_tool([p3_result("1960s")]),
    )

    result = await P3LifePeriodGap().produce(db_pool, UUID(person_id), stub_settings)

    assert result.source_tag == "life_period_gap"
    assert result.questions[0].attributes["life_period"] == "1960s"
    assert result.questions[0].themes == ["era"]

