"""Tests for context.build_context (DB-touching)."""

from __future__ import annotations

from psycopg.types.json import Json

from flashback.workers.trait_synthesizer.context import (
    build_context,
    render_user_message,
)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_trait(
    db_pool,
    *,
    person_id: str,
    name: str,
    description: str | None = "desc",
    strength: str = "mentioned_once",
    status: str = "active",
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO traits (person_id, name, description, strength, status)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (person_id, name, description, strength, status),
            )
            tid = cur.fetchone()[0]
            conn.commit()
    return tid


def _seed_thread(
    db_pool,
    *,
    person_id: str,
    name: str,
    description: str = "desc",
    status: str = "active",
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO threads (person_id, name, description, status)
                VALUES (%s, %s, %s, %s)
                RETURNING id::text
                """,
                (person_id, name, description, status),
            )
            tid = cur.fetchone()[0]
            conn.commit()
    return tid


def _seed_moment(
    db_pool,
    *,
    person_id: str,
    title: str = "t",
    narrative: str = "n",
    status: str = "active",
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments (person_id, title, narrative, status)
                VALUES (%s, %s, %s, %s)
                RETURNING id::text
                """,
                (person_id, title, narrative, status),
            )
            mid = cur.fetchone()[0]
            conn.commit()
    return mid


def _seed_edge(
    db_pool,
    *,
    from_kind: str,
    from_id: str,
    to_kind: str,
    to_id: str,
    edge_type: str,
    status: str = "active",
) -> None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO edges (from_kind, from_id, to_kind, to_id,
                                   edge_type, attributes, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (from_kind, from_id, to_kind, to_id, edge_type, Json({}), status),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_context_pulls_only_active(db_pool, make_person) -> None:
    person_id = make_person("Subject Active")

    # Active trait + archived trait
    active_trait = _seed_trait(db_pool, person_id=person_id, name="Active Trait")
    _seed_trait(
        db_pool,
        person_id=person_id,
        name="Archived Trait",
        status="archived",
    )

    # Active thread + archived thread
    active_thread = _seed_thread(db_pool, person_id=person_id, name="Active Thread")
    _seed_thread(
        db_pool,
        person_id=person_id,
        name="Archived Thread",
        status="archived",
    )

    ctx = build_context(db_pool, person_id=person_id)

    assert ctx.person_name == "Subject Active"
    assert [t.id for t in ctx.existing_traits] == [active_trait]
    assert [t.id for t in ctx.threads] == [active_thread]


def test_build_context_cross_person_isolation(db_pool, make_person) -> None:
    a = make_person("A")
    b = make_person("B")

    a_trait = _seed_trait(db_pool, person_id=a, name="Trait A")
    _seed_trait(db_pool, person_id=b, name="Trait B")
    a_thread = _seed_thread(db_pool, person_id=a, name="Thread A")
    _seed_thread(db_pool, person_id=b, name="Thread B")

    ctx_a = build_context(db_pool, person_id=a)
    assert {t.id for t in ctx_a.existing_traits} == {a_trait}
    assert {t.id for t in ctx_a.threads} == {a_thread}


def test_build_context_moment_count_per_thread(db_pool, make_person) -> None:
    person_id = make_person("Counts")
    thread_id = _seed_thread(db_pool, person_id=person_id, name="Cabin")

    # 3 active moments evidencing the thread + 1 superseded one (must NOT count)
    for i in range(3):
        mid = _seed_moment(db_pool, person_id=person_id, title=f"m{i}")
        _seed_edge(
            db_pool,
            from_kind="moment",
            from_id=mid,
            to_kind="thread",
            to_id=thread_id,
            edge_type="evidences",
        )
    superseded = _seed_moment(
        db_pool, person_id=person_id, title="old", status="superseded"
    )
    _seed_edge(
        db_pool,
        from_kind="moment",
        from_id=superseded,
        to_kind="thread",
        to_id=thread_id,
        edge_type="evidences",
    )

    ctx = build_context(db_pool, person_id=person_id)
    assert len(ctx.threads) == 1
    assert ctx.threads[0].moment_count == 3


def test_build_context_moment_count_per_trait(db_pool, make_person) -> None:
    person_id = make_person("Trait Counts")
    trait_id = _seed_trait(db_pool, person_id=person_id, name="Patient")
    # 2 moments exemplify the trait
    for i in range(2):
        mid = _seed_moment(db_pool, person_id=person_id, title=f"m{i}")
        _seed_edge(
            db_pool,
            from_kind="moment",
            from_id=mid,
            to_kind="trait",
            to_id=trait_id,
            edge_type="exemplifies",
        )

    ctx = build_context(db_pool, person_id=person_id)
    assert ctx.existing_traits[0].moment_count == 2


def test_build_context_archived_edges_are_ignored(db_pool, make_person) -> None:
    person_id = make_person("Archived edges")
    trait_id = _seed_trait(db_pool, person_id=person_id, name="Quiet")
    mid = _seed_moment(db_pool, person_id=person_id, title="m")
    _seed_edge(
        db_pool,
        from_kind="moment",
        from_id=mid,
        to_kind="trait",
        to_id=trait_id,
        edge_type="exemplifies",
        status="archived",
    )

    ctx = build_context(db_pool, person_id=person_id)
    # Edge is archived, so the count is 0.
    assert ctx.existing_traits[0].moment_count == 0


def test_render_user_message_handles_empty(db_pool, make_person) -> None:
    person_id = make_person("Empty")
    ctx = build_context(db_pool, person_id=person_id)
    msg = render_user_message(ctx)
    assert "<existing_traits>" in msg and "(none)" in msg
    assert "<threads>" in msg
    assert "Empty" in msg


def test_render_user_message_includes_ids(db_pool, make_person) -> None:
    person_id = make_person("Render")
    trait_id = _seed_trait(db_pool, person_id=person_id, name="Generous")
    thread_id = _seed_thread(db_pool, person_id=person_id, name="Cabin")
    ctx = build_context(db_pool, person_id=person_id)
    msg = render_user_message(ctx)
    assert f"trait_id={trait_id}" in msg
    assert f"thread_id={thread_id}" in msg
