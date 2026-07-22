"""Repository: cast of involved person-entities + genders for storybook
context (gender-hallucination fix, Task 3)."""

from __future__ import annotations

from flashback.storybook.repository import fetch_involved_people_async


async def _make_person(pool) -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name, relationship) "
                    "VALUES ('Dad', 'father') RETURNING id::text"
                )
                return (await cur.fetchone())[0]


async def _make_moment(pool, pid: str, title: str) -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO moments (person_id, title, narrative) "
                    "VALUES (%s, %s, 'n') RETURNING id::text",
                    (pid, title),
                )
                return (await cur.fetchone())[0]


async def _make_entity(
    pool, pid: str, *, kind: str, name: str,
    gender: str | None = None, relationship: str | None = None,
    status: str = "active",
) -> str:
    import json

    attrs: dict[str, str] = {}
    if gender is not None:
        attrs["gender"] = gender
    if relationship is not None:
        attrs["relationship"] = relationship
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO entities (person_id, kind, name, attributes, status) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id::text",
                    (pid, kind, name, json.dumps(attrs), status),
                )
                return (await cur.fetchone())[0]


async def _link(pool, moment_id: str, entity_id: str, *, status: str = "active") -> None:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO edges (from_kind, from_id, to_kind, to_id, "
                    "edge_type, status) VALUES ('moment', %s, 'entity', %s, "
                    "'involves', %s)",
                    (moment_id, entity_id, status),
                )


async def test_fetch_involved_people_returns_person_entities_with_gender(
    async_pool,
) -> None:
    pid = await _make_person(async_pool)
    m1 = await _make_moment(async_pool, pid, "m1")
    aarav = await _make_entity(
        async_pool, pid, kind="person", name="Aarav",
        gender="male", relationship="son",
    )
    riverbank = await _make_entity(async_pool, pid, kind="place", name="Riverbank")
    await _link(async_pool, m1, aarav)
    await _link(async_pool, m1, riverbank)

    async with async_pool.connection() as conn, conn.cursor() as cur:
        people = await fetch_involved_people_async(
            cur, person_id=pid, moment_ids=[m1]
        )

    names = {p["name"] for p in people}
    assert "Aarav" in names
    assert "Riverbank" not in names  # place kind excluded

    aarav_row = next(p for p in people if p["name"] == "Aarav")
    assert aarav_row["gender"] == "male"
    assert aarav_row["relationship"] == "son"


async def test_fetch_involved_people_empty_moment_ids_returns_empty(
    async_pool,
) -> None:
    pid = await _make_person(async_pool)
    async with async_pool.connection() as conn, conn.cursor() as cur:
        people = await fetch_involved_people_async(
            cur, person_id=pid, moment_ids=[]
        )
    assert people == []


async def test_fetch_involved_people_dedupes_across_moments(async_pool) -> None:
    pid = await _make_person(async_pool)
    m1 = await _make_moment(async_pool, pid, "m1")
    m2 = await _make_moment(async_pool, pid, "m2")
    aarav = await _make_entity(
        async_pool, pid, kind="person", name="Aarav", gender="male"
    )
    await _link(async_pool, m1, aarav)
    await _link(async_pool, m2, aarav)

    async with async_pool.connection() as conn, conn.cursor() as cur:
        people = await fetch_involved_people_async(
            cur, person_id=pid, moment_ids=[m1, m2]
        )

    assert len(people) == 1
    assert people[0]["name"] == "Aarav"


async def test_fetch_involved_people_ordered_by_name(async_pool) -> None:
    pid = await _make_person(async_pool)
    m1 = await _make_moment(async_pool, pid, "m1")
    zoe = await _make_entity(async_pool, pid, kind="person", name="Zoe", gender="female")
    aarav = await _make_entity(async_pool, pid, kind="person", name="Aarav", gender="male")
    await _link(async_pool, m1, zoe)
    await _link(async_pool, m1, aarav)

    async with async_pool.connection() as conn, conn.cursor() as cur:
        people = await fetch_involved_people_async(
            cur, person_id=pid, moment_ids=[m1]
        )

    assert [p["name"] for p in people] == ["Aarav", "Zoe"]


async def test_fetch_involved_people_excludes_inactive_entity_and_edge(
    async_pool,
) -> None:
    pid = await _make_person(async_pool)
    m1 = await _make_moment(async_pool, pid, "m1")
    merged = await _make_entity(
        async_pool, pid, kind="person", name="MergedAway", status="merged"
    )
    await _link(async_pool, m1, merged)

    archived_edge_target = await _make_entity(
        async_pool, pid, kind="person", name="ArchivedEdge"
    )
    await _link(async_pool, m1, archived_edge_target, status="archived")

    async with async_pool.connection() as conn, conn.cursor() as cur:
        people = await fetch_involved_people_async(
            cur, person_id=pid, moment_ids=[m1]
        )

    assert people == []
