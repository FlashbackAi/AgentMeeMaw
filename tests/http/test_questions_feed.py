"""``GET /questions/feed`` route tests.

DB-touching; skipped when ``TEST_DATABASE_URL`` is unset. Seeds a person
and two active producer-bank questions directly, then hits the endpoint.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from tests.http.conftest import auth_headers

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set; skipping DB-touching HTTP test.",
)


async def _seed_person_and_questions(async_db_pool):
    person_id = uuid4()
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO persons (id, name, relationship, phase)
                VALUES (%s, %s, %s, 'steady')
                """,
                (str(person_id), "Ishita", "mother"),
            )
            await cur.execute(
                """
                INSERT INTO questions (id, person_id, text, source, status, attributes)
                VALUES
                  (%s, %s, %s, 'dropped_reference', 'active', %s),
                  (%s, %s, %s, 'life_period_gap',   'active', %s)
                """,
                (
                    str(uuid4()), str(person_id), "Tell me about the bike.",
                    '{"themes": ["family"]}',
                    str(uuid4()), str(person_id), "What was your first job?",
                    '{"themes": ["career"]}',
                ),
            )
        await conn.commit()
    return person_id


async def test_questions_feed_returns_ranked_producer_questions(
    client_with_db, async_db_pool
):
    person_id = await _seed_person_and_questions(async_db_pool)
    resp = await client_with_db.get(
        f"/questions/feed?person_id={person_id}",
        headers=auth_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    sources = {q["source"] for q in body["questions"]}
    assert sources == {"dropped_reference", "life_period_gap"}
    # dropped_reference outranks life_period_gap at equal age.
    assert body["questions"][0]["source"] == "dropped_reference"


async def test_questions_feed_empty_for_unknown_person(client_with_db):
    resp = await client_with_db.get(
        f"/questions/feed?person_id={uuid4()}",
        headers=auth_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"questions": []}
