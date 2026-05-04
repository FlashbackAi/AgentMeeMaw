"""P2 underdeveloped-entity producer tests."""

from __future__ import annotations

from uuid import UUID

from flashback.workers.producers import underdeveloped as p2_mod
from flashback.workers.producers.underdeveloped import P2Underdeveloped

from tests.workers.producers.conftest import (
    queued_call_with_tool,
    seed_edge,
    seed_entity,
    seed_moment,
)
from tests.workers.producers.fixtures.sample_states import p2_result


def test_entities_with_fewer_than_three_mentions_are_surfaced(
    db_pool, make_person, stub_settings
) -> None:
    person_id = make_person("P2")
    thin = seed_entity(db_pool, person_id=person_id, name="Thin")
    rich = seed_entity(db_pool, person_id=person_id, name="Rich")
    for _ in range(3):
        mid = seed_moment(db_pool, person_id=person_id)
        seed_edge(
            db_pool,
            from_kind="moment",
            from_id=mid,
            to_kind="entity",
            to_id=rich,
            edge_type="involves",
        )

    found = P2Underdeveloped()._find_underdeveloped(
        db_pool, UUID(person_id), stub_settings
    )

    assert [str(e.id) for e in found] == [thin]


def test_sort_order_and_cap(db_pool, make_person, stub_settings) -> None:
    person_id = make_person("P2 sort")
    long_zero = seed_entity(
        db_pool, person_id=person_id, name="Long", description="x" * 100
    )
    short_zero = seed_entity(
        db_pool, person_id=person_id, name="Short", description="x"
    )
    one_mention = seed_entity(db_pool, person_id=person_id, name="One")
    mid = seed_moment(db_pool, person_id=person_id)
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=mid,
        to_kind="entity",
        to_id=one_mention,
        edge_type="involves",
    )
    stub_settings.p2_max_entities_per_run = 2

    found = P2Underdeveloped()._find_underdeveloped(
        db_pool, UUID(person_id), stub_settings
    )

    assert [str(e.id) for e in found] == [short_zero, long_zero]


def test_cross_person_isolation(db_pool, make_person, stub_settings) -> None:
    p1 = make_person("One")
    p2 = make_person("Two")
    own = seed_entity(db_pool, person_id=p1, name="Own")
    seed_entity(db_pool, person_id=p2, name="Other")

    found = P2Underdeveloped()._find_underdeveloped(db_pool, UUID(p1), stub_settings)

    assert [str(e.id) for e in found] == [own]


async def test_llm_happy_path(db_pool, make_person, stub_settings, monkeypatch) -> None:
    person_id = make_person("P2 llm")
    entity_id = seed_entity(db_pool, person_id=person_id, name="Uncle Raj")
    monkeypatch.setattr(
        p2_mod,
        "call_with_tool",
        queued_call_with_tool([p2_result(entity_id)]),
    )

    result = await P2Underdeveloped().produce(db_pool, UUID(person_id), stub_settings)

    assert result.source_tag == "underdeveloped_entity"
    assert len(result.questions) == 1
    assert str(result.questions[0].targets_entity_id) == entity_id
    assert result.questions[0].themes

