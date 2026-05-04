"""Tests for context.build_context (DB-touching)."""

from __future__ import annotations

from flashback.workers.profile_summary.context import (
    build_context,
    render_context,
)

from tests.workers.profile_summary.fixtures.sample_profiles import (
    seed_edge,
    seed_entity,
    seed_moment,
    seed_thread,
    seed_trait,
)


# ---------------------------------------------------------------------------
# Trait ordering
# ---------------------------------------------------------------------------


def test_fetch_top_traits_orders_by_strength_then_updated_at(
    db_pool, make_person, top_caps
) -> None:
    person_id = make_person("Order")
    # Insert in deliberately mixed order; strength rank should win.
    seed_trait(db_pool, person_id=person_id, name="Mid1", strength="moderate")
    seed_trait(db_pool, person_id=person_id, name="Defining", strength="defining")
    seed_trait(db_pool, person_id=person_id, name="Strong", strength="strong")
    seed_trait(db_pool, person_id=person_id, name="Mid2", strength="moderate")
    seed_trait(db_pool, person_id=person_id, name="Once", strength="mentioned_once")

    ctx = build_context(db_pool, person_id=person_id, **top_caps)
    names = [t.name for t in ctx.traits]
    # Strength: defining > strong > moderate > mentioned_once.
    assert names[0] == "Defining"
    assert names[1] == "Strong"
    # Among the two moderates, the more recently updated one (Mid2) wins.
    assert names.index("Mid2") < names.index("Mid1")
    assert names[-1] == "Once"


def test_fetch_top_traits_respects_limit(db_pool, make_person) -> None:
    person_id = make_person("Limit")
    for i in range(10):
        seed_trait(
            db_pool,
            person_id=person_id,
            name=f"Trait {i}",
            strength="mentioned_once",
        )
    ctx = build_context(
        db_pool,
        person_id=person_id,
        top_traits_max=3,
        top_threads_max=5,
        top_entities_max=8,
    )
    assert len(ctx.traits) == 3


def test_fetch_top_traits_excludes_archived(db_pool, make_person, top_caps) -> None:
    person_id = make_person("Arc")
    active = seed_trait(db_pool, person_id=person_id, name="Active")
    seed_trait(db_pool, person_id=person_id, name="Archived", status="archived")
    ctx = build_context(db_pool, person_id=person_id, **top_caps)
    assert [t.name for t in ctx.traits] == ["Active"]
    assert active  # parameter just to silence linter


# ---------------------------------------------------------------------------
# Thread filtering
# ---------------------------------------------------------------------------


def test_fetch_top_threads_excludes_zero_evidence(
    db_pool, make_person, top_caps
) -> None:
    """Threads with zero evidencing active moments are filtered out."""
    person_id = make_person("Zero")
    th_empty = seed_thread(db_pool, person_id=person_id, name="Empty")
    th_one = seed_thread(db_pool, person_id=person_id, name="One")
    m = seed_moment(db_pool, person_id=person_id)
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m,
        to_kind="thread",
        to_id=th_one,
        edge_type="evidences",
    )
    ctx = build_context(db_pool, person_id=person_id, **top_caps)
    names = [t.name for t in ctx.threads]
    assert "One" in names
    assert "Empty" not in names
    assert th_empty  # silence unused


def test_fetch_top_threads_orders_by_count_desc(
    db_pool, make_person, top_caps
) -> None:
    person_id = make_person("ThreadOrder")
    th_a = seed_thread(db_pool, person_id=person_id, name="A")
    th_b = seed_thread(db_pool, person_id=person_id, name="B")
    # B has 3 moments, A has 1.
    for _ in range(3):
        m = seed_moment(db_pool, person_id=person_id)
        seed_edge(
            db_pool,
            from_kind="moment",
            from_id=m,
            to_kind="thread",
            to_id=th_b,
            edge_type="evidences",
        )
    m_a = seed_moment(db_pool, person_id=person_id)
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m_a,
        to_kind="thread",
        to_id=th_a,
        edge_type="evidences",
    )
    ctx = build_context(db_pool, person_id=person_id, **top_caps)
    assert [t.name for t in ctx.threads] == ["B", "A"]
    assert ctx.threads[0].moment_count == 3
    assert ctx.threads[1].moment_count == 1


def test_fetch_top_threads_respects_limit(db_pool, make_person) -> None:
    person_id = make_person("TLimit")
    for i in range(10):
        th = seed_thread(db_pool, person_id=person_id, name=f"T{i}")
        m = seed_moment(db_pool, person_id=person_id)
        seed_edge(
            db_pool,
            from_kind="moment",
            from_id=m,
            to_kind="thread",
            to_id=th,
            edge_type="evidences",
        )
    ctx = build_context(
        db_pool,
        person_id=person_id,
        top_traits_max=7,
        top_threads_max=2,
        top_entities_max=8,
    )
    assert len(ctx.threads) == 2


# ---------------------------------------------------------------------------
# Entity filtering
# ---------------------------------------------------------------------------


def test_fetch_top_entities_excludes_zero_mentions(
    db_pool, make_person, top_caps
) -> None:
    person_id = make_person("EntZero")
    e_used = seed_entity(db_pool, person_id=person_id, name="Used")
    e_unused = seed_entity(db_pool, person_id=person_id, name="Unused")
    m = seed_moment(db_pool, person_id=person_id)
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m,
        to_kind="entity",
        to_id=e_used,
        edge_type="involves",
    )
    ctx = build_context(db_pool, person_id=person_id, **top_caps)
    names = [e.name for e in ctx.entities]
    assert "Used" in names
    assert "Unused" not in names
    assert e_unused  # silence unused


def test_fetch_top_entities_orders_by_mentions_desc(
    db_pool, make_person, top_caps
) -> None:
    person_id = make_person("EntOrder")
    e_a = seed_entity(db_pool, person_id=person_id, name="A")
    e_b = seed_entity(db_pool, person_id=person_id, name="B")
    for _ in range(3):
        m = seed_moment(db_pool, person_id=person_id)
        seed_edge(
            db_pool,
            from_kind="moment",
            from_id=m,
            to_kind="entity",
            to_id=e_b,
            edge_type="involves",
        )
    m_a = seed_moment(db_pool, person_id=person_id)
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m_a,
        to_kind="entity",
        to_id=e_a,
        edge_type="involves",
    )
    ctx = build_context(db_pool, person_id=person_id, **top_caps)
    assert [e.name for e in ctx.entities] == ["B", "A"]


# ---------------------------------------------------------------------------
# Cross-person isolation
# ---------------------------------------------------------------------------


def test_build_context_cross_person_isolation(
    db_pool, make_person, top_caps
) -> None:
    a = make_person("Alice")
    b = make_person("Bob")
    seed_trait(db_pool, person_id=a, name="A trait")
    seed_trait(db_pool, person_id=b, name="B trait")
    th_a = seed_thread(db_pool, person_id=a, name="A thread")
    th_b = seed_thread(db_pool, person_id=b, name="B thread")
    e_a = seed_entity(db_pool, person_id=a, name="A entity")
    e_b = seed_entity(db_pool, person_id=b, name="B entity")
    m_a = seed_moment(db_pool, person_id=a)
    m_b = seed_moment(db_pool, person_id=b)
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m_a,
        to_kind="thread",
        to_id=th_a,
        edge_type="evidences",
    )
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m_b,
        to_kind="thread",
        to_id=th_b,
        edge_type="evidences",
    )
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m_a,
        to_kind="entity",
        to_id=e_a,
        edge_type="involves",
    )
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m_b,
        to_kind="entity",
        to_id=e_b,
        edge_type="involves",
    )

    ctx_a = build_context(db_pool, person_id=a, **top_caps)
    assert {t.name for t in ctx_a.traits} == {"A trait"}
    assert {t.name for t in ctx_a.threads} == {"A thread"}
    assert {e.name for e in ctx_a.entities} == {"A entity"}


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_render_context_omits_empty_sections(
    db_pool, make_person, top_caps
) -> None:
    person_id = make_person("Sparse")
    ctx = build_context(db_pool, person_id=person_id, **top_caps)
    out = render_context(ctx)
    # Subject is always present.
    assert "<subject>" in out
    assert "Sparse" in out
    # Empty sections are omitted entirely.
    assert "<traits>" not in out
    assert "<threads>" not in out
    assert "<entities>" not in out


def test_render_context_includes_all_sections_when_populated(
    db_pool, make_person, top_caps
) -> None:
    person_id = make_person("Full")
    seed_trait(db_pool, person_id=person_id, name="Generous", strength="defining")
    th = seed_thread(db_pool, person_id=person_id, name="Workshop")
    e = seed_entity(db_pool, person_id=person_id, name="Margaret")
    m = seed_moment(
        db_pool,
        person_id=person_id,
        time_anchor={"year": 1962},
        life_period_estimate="parenthood",
    )
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m,
        to_kind="thread",
        to_id=th,
        edge_type="evidences",
    )
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m,
        to_kind="entity",
        to_id=e,
        edge_type="involves",
    )

    ctx = build_context(db_pool, person_id=person_id, **top_caps)
    out = render_context(ctx)
    assert "<traits>" in out and "Generous" in out
    assert "<threads>" in out and "Workshop" in out
    assert "<entities>" in out and "Margaret" in out
    assert "<time_period>" in out and "1962" in out and "parenthood" in out
