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


# P2 now only surfaces entities with importance_score >= 2 (subject-centered):
# +1 for a subject mention in the description, +1 for a subject-context word
# (friend/worked/lived/...). A bare supporting person with no such context is
# intentionally NOT surfaced. So qualifying descriptions reference the subject
# AND a context word.
def _qual_desc(subject: str, extra: str = "") -> str:
    return f"{subject}'s close friend who worked alongside {subject}{extra}"


def test_qualifying_entity_under_three_mentions_is_surfaced(
    db_pool, make_person, stub_settings
) -> None:
    subject = "P2"
    person_id = make_person(subject)
    qualifying = seed_entity(
        db_pool, person_id=person_id, name="Friend", description=_qual_desc(subject)
    )
    # Bare person with no subject context -> importance below threshold -> dropped.
    seed_entity(db_pool, person_id=person_id, name="Incidental")
    # Three mentions -> excluded by the "fewer than three mentions" filter.
    rich = seed_entity(
        db_pool, person_id=person_id, name="Rich", description=_qual_desc(subject)
    )
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
        db_pool, UUID(person_id), stub_settings, subject_name=subject
    )

    assert [str(e.id) for e in found] == [qualifying]


def test_sort_order_and_cap(db_pool, make_person, stub_settings) -> None:
    subject = "P2sort"
    person_id = make_person(subject)
    # Two qualifying entities (importance 2, 0 mentions) of different
    # description length; sort tiebreak is ascending len(description).
    long_q = seed_entity(
        db_pool, person_id=person_id, name="Long",
        description=_qual_desc(subject, extra=" " + "x" * 80),
    )
    short_q = seed_entity(
        db_pool, person_id=person_id, name="Short", description=_qual_desc(subject)
    )
    # A third qualifying entity (mid-length) to exercise the cap.
    mid_q = seed_entity(
        db_pool, person_id=person_id, name="Mid",
        description=_qual_desc(subject, extra=" " + "x" * 40),
    )
    stub_settings.p2_max_entities_per_run = 2

    found = P2Underdeveloped()._find_underdeveloped(
        db_pool, UUID(person_id), stub_settings, subject_name=subject
    )

    # Same importance + 0 mentions -> shortest descriptions first; capped at 2.
    assert [str(e.id) for e in found] == [short_q, mid_q]


def test_cross_person_isolation(db_pool, make_person, stub_settings) -> None:
    s1, s2 = "OneSubj", "TwoSubj"
    p1 = make_person(s1)
    p2 = make_person(s2)
    own = seed_entity(
        db_pool, person_id=p1, name="Own", description=_qual_desc(s1)
    )
    seed_entity(db_pool, person_id=p2, name="Other", description=_qual_desc(s2))

    found = P2Underdeveloped()._find_underdeveloped(
        db_pool, UUID(p1), stub_settings, subject_name=s1
    )

    assert [str(e.id) for e in found] == [own]


async def test_llm_happy_path(db_pool, make_person, stub_settings, monkeypatch) -> None:
    subject = "P2llm"
    person_id = make_person(subject)
    entity_id = seed_entity(
        db_pool, person_id=person_id, name="Uncle Raj", description=_qual_desc(subject)
    )
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

