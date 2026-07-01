"""SP5: get_same_event_linked_moments returns active partner moments."""

import os

import pytest

from tests.retrieval.conftest import insert_person

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


async def _two_moments(pool, person):
    ids = []
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for t in ("A", "B"):
                await cur.execute(
                    "INSERT INTO moments (person_id, title, narrative, status) "
                    "VALUES (%s, %s, 'n', 'active') RETURNING id::text",
                    (person, t),
                )
                ids.append((await cur.fetchone())[0])
            await conn.commit()
    return ids[0], ids[1]


async def _link(pool, person, a, b, status="active"):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO moment_same_event_links "
                "(person_id, moment_a_id, moment_b_id, status) VALUES (%s, %s, %s, %s)",
                (person, a, b, status),
            )
            await conn.commit()


@db_only
async def test_returns_active_partner_moments(async_db_pool, retrieval_service):
    person = await insert_person(async_db_pool, "Subj")
    mA, mB = await _two_moments(async_db_pool, person)
    await _link(async_db_pool, person, mA, mB)

    out = await retrieval_service.get_same_event_linked_moments(person, [mA])
    assert [m.title for m in out] == ["B"]


@db_only
async def test_unlinked_excluded(async_db_pool, retrieval_service):
    person = await insert_person(async_db_pool, "Subj")
    mA, mB = await _two_moments(async_db_pool, person)
    await _link(async_db_pool, person, mA, mB, status="unlinked")

    out = await retrieval_service.get_same_event_linked_moments(person, [mA])
    assert out == []


@db_only
async def test_empty_moment_ids_returns_empty(async_db_pool, retrieval_service):
    person = await insert_person(async_db_pool, "Subj")
    out = await retrieval_service.get_same_event_linked_moments(person, [])
    assert out == []
