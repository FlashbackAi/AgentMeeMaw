"""Reusable seeders for the Profile Summary tests.

Each helper writes the canonical-graph rows the runner reads (traits,
threads, entities, moments, edges) for one person and returns the
ids the test needs to assert against.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg.types.json import Json


# ---------------------------------------------------------------------------
# Low-level inserts
# ---------------------------------------------------------------------------


def seed_trait(
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


def seed_thread(
    db_pool,
    *,
    person_id: str,
    name: str = "thread",
    description: str = "d",
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


def seed_entity(
    db_pool,
    *,
    person_id: str,
    name: str,
    kind: str = "person",
    description: str | None = "an entity",
    status: str = "active",
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entities (person_id, kind, name, description, status)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (person_id, kind, name, description, status),
            )
            eid = cur.fetchone()[0]
            conn.commit()
    return eid


def seed_moment(
    db_pool,
    *,
    person_id: str,
    title: str = "t",
    narrative: str = "n",
    time_anchor: dict | None = None,
    life_period_estimate: str | None = None,
    status: str = "active",
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments
                       (person_id, title, narrative, time_anchor,
                        life_period_estimate, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (
                    person_id,
                    title,
                    narrative,
                    Json(time_anchor) if time_anchor is not None else None,
                    life_period_estimate,
                    status,
                ),
            )
            mid = cur.fetchone()[0]
            conn.commit()
    return mid


def seed_edge(
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
# Higher-level "rich profile" helper
# ---------------------------------------------------------------------------


@dataclass
class SeededProfile:
    person_id: str
    trait_ids: list[str]
    thread_ids: list[str]
    entity_ids: list[str]
    moment_ids: list[str]


def seed_rich_profile(db_pool, person_id: str) -> SeededProfile:
    """Seed a moderately full legacy: 3 traits, 2 threads, 2 entities,
    3 moments, with appropriate edges and time anchors. Used for the
    happy-path runner test."""
    t_def = seed_trait(
        db_pool,
        person_id=person_id,
        name="Quietly generous",
        description="Always sharing without asking for credit.",
        strength="defining",
    )
    t_strong = seed_trait(
        db_pool,
        person_id=person_id,
        name="Patient teacher",
        description="Took the time to explain.",
        strength="strong",
    )
    t_mod = seed_trait(
        db_pool,
        person_id=person_id,
        name="Quick to laugh",
        description="Easy laugh in the kitchen.",
        strength="moderate",
    )

    th_workshop = seed_thread(
        db_pool,
        person_id=person_id,
        name="The workshop",
        description="The garage workshop where he rebuilt motorcycles.",
    )
    th_summers = seed_thread(
        db_pool,
        person_id=person_id,
        name="Cabin summers",
        description="Family summers at the lake cabin.",
    )

    e_wife = seed_entity(
        db_pool,
        person_id=person_id,
        kind="person",
        name="Margaret",
        description="His wife of fifty years.",
    )
    e_cabin = seed_entity(
        db_pool,
        person_id=person_id,
        kind="place",
        name="The Lake Cabin",
        description="Family cabin in northern Minnesota.",
    )

    m1 = seed_moment(
        db_pool,
        person_id=person_id,
        title="rebuilding the indian",
        narrative="The summer he and his brother rebuilt the Indian.",
        time_anchor={"year": 1962},
        life_period_estimate="young adult",
    )
    m2 = seed_moment(
        db_pool,
        person_id=person_id,
        title="cabin dock",
        narrative="The dock at the cabin every July.",
        time_anchor={"year": 1985},
        life_period_estimate="parenthood",
    )
    m3 = seed_moment(
        db_pool,
        person_id=person_id,
        title="garage hours",
        narrative="Weekends in the garage with the radio on.",
        time_anchor={"year": 2003},
        life_period_estimate="retirement",
    )

    # Thread → moment evidences (we count active_edges from moment to thread)
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m1,
        to_kind="thread",
        to_id=th_workshop,
        edge_type="evidences",
    )
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m3,
        to_kind="thread",
        to_id=th_workshop,
        edge_type="evidences",
    )
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m2,
        to_kind="thread",
        to_id=th_summers,
        edge_type="evidences",
    )

    # Moment → entity involves
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m2,
        to_kind="entity",
        to_id=e_wife,
        edge_type="involves",
    )
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m2,
        to_kind="entity",
        to_id=e_cabin,
        edge_type="involves",
    )
    seed_edge(
        db_pool,
        from_kind="moment",
        from_id=m3,
        to_kind="entity",
        to_id=e_wife,
        edge_type="involves",
    )

    return SeededProfile(
        person_id=person_id,
        trait_ids=[t_def, t_strong, t_mod],
        thread_ids=[th_workshop, th_summers],
        entity_ids=[e_wife, e_cabin],
        moment_ids=[m1, m2, m3],
    )
