"""P5 universal coverage producer tests."""

from __future__ import annotations

from uuid import UUID

from flashback.workers.producers import universal as p5_mod
from flashback.workers.producers.universal import P5UniversalCoverage

from tests.workers.producers.conftest import (
    queued_call_with_tool,
    seed_moment,
    seed_thread,
)
from tests.workers.producers.fixtures.sample_states import p5_result


def test_keywords_in_moments_and_threads_count(db_pool, make_person, stub_settings):
    person_id = make_person("Universal")
    seed_moment(
        db_pool,
        person_id=person_id,
        title="Kitchen",
        narrative="She loved to cook every morning.",
    )
    seed_thread(
        db_pool,
        person_id=person_id,
        name="Faith",
        description="Church and prayer shaped the family.",
    )
    stub_settings.p5_dimension_coverage_threshold = 1

    under = P5UniversalCoverage()._find_under_covered(
        db_pool, UUID(person_id), stub_settings
    )

    names = {item.name for item in under}
    assert "food" not in names
    assert "faiths" not in names


def test_under_covered_sort_and_cap(db_pool, make_person, stub_settings):
    person_id = make_person("Universal cap")
    seed_moment(db_pool, person_id=person_id, narrative="family mother father")
    seed_thread(db_pool, person_id=person_id, description="brother and sister")
    stub_settings.p5_dimension_coverage_threshold = 3
    stub_settings.p5_max_dimensions_per_run = 3

    under = P5UniversalCoverage()._find_under_covered(
        db_pool, UUID(person_id), stub_settings
    )

    assert len(under) == 3
    assert [item.coverage_count for item in under] == [0, 0, 0]


async def test_llm_happy_path(db_pool, make_person, stub_settings, monkeypatch):
    person_id = make_person("P5 llm")
    monkeypatch.setattr(
        p5_mod,
        "call_with_tool",
        queued_call_with_tool([p5_result("childhood")]),
    )

    result = await P5UniversalCoverage().produce(
        db_pool, UUID(person_id), stub_settings
    )

    assert result.source_tag == "universal_dimension"
    assert result.questions[0].attributes["dimension"] == "childhood"
    assert result.questions[0].themes == ["childhood"]

