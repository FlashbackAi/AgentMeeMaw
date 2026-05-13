from __future__ import annotations

import os
import socket
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest

from tests.http.conftest import auth_headers

def _postgres_reachable(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 5432
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _TEST_DATABASE_URL or not _postgres_reachable(_TEST_DATABASE_URL),
    reason="TEST_DATABASE_URL unavailable; skipping DB-touching onboarding tests.",
)


def person_payload(**overrides):
    payload = {
        "name": "Maya",
        "relationship": "daughter",
        "contributor_display_name": "Sarah",
    }
    payload.update(overrides)
    return payload


async def _ensure_person_roles_table(async_db_pool) -> None:
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS person_roles (
                    id UUID PRIMARY KEY,
                    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
                    relationship TEXT,
                    onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
                    archetype_answers JSONB NOT NULL DEFAULT '[]'::jsonb
                )
                """
            )
        await conn.commit()


async def _create_friend_role(client_with_db, async_db_pool) -> tuple[str, str]:
    await _ensure_person_roles_table(async_db_pool)
    person_resp = await client_with_db.post(
        "/persons",
        headers=auth_headers(),
        json=person_payload(
            name="Chitanya",
            relationship="friend",
            contributor_display_name="Mokshith",
        ),
    )
    assert person_resp.status_code == 200, person_resp.text
    person_id = person_resp.json()["person_id"]
    role_id = str(uuid4())
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO person_roles (id, person_id, relationship)
                VALUES (%s, %s, %s)
                """,
                (role_id, person_id, "friend"),
            )
        await conn.commit()
    return person_id, role_id


class TestArchetypeQuestions:
    async def test_returns_public_questions_without_implies(
        self, client_with_db, async_db_pool
    ) -> None:
        _, role_id = await _create_friend_role(client_with_db, async_db_pool)

        resp = await client_with_db.get(
            "/api/v1/onboarding/archetype-questions",
            headers=auth_headers(),
            params={"role_id": role_id},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["relationship"] == "friend"
        assert body["archetype"] == "friend"
        assert 3 <= len(body["questions"]) <= 5
        assert [q["id"] for q in body["questions"][:3]] == [
            "friend_meet",
            "friend_first_impression",
            "friend_shared_place",
        ]
        assert "implies" not in body["questions"][0]["options"][0]


class TestArchetypeAnswers:
    async def test_persists_answers_entities_and_coverage(
        self, client_with_db, async_db_pool
    ) -> None:
        person_id, role_id = await _create_friend_role(client_with_db, async_db_pool)

        resp = await client_with_db.post(
            "/api/v1/onboarding/archetype-answers",
            headers=auth_headers(),
            json={
                "role_id": role_id,
                "answers": [
                    {"question_id": "friend_meet", "option_id": "school"},
                    {
                        "question_id": "friend_first_impression",
                        "option_id": "confidence",
                    },
                    {"question_id": "friend_shared_place", "skipped": True},
                    {"question_id": "friend_what_drew_you", "skipped": True},
                ],
            },
        )

        assert resp.status_code == 200, resp.text
        UUID(resp.json()["session_id"])

        async with async_db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT onboarding_complete, archetype_answers
                    FROM person_roles
                    WHERE id = %s
                    """,
                    (role_id,),
                )
                role_row = await cur.fetchone()
                await cur.execute(
                    """
                    SELECT coverage_state
                    FROM persons
                    WHERE id = %s
                    """,
                    (person_id,),
                )
                coverage_row = await cur.fetchone()
                await cur.execute(
                    """
                    SELECT kind, name, attributes
                    FROM entities
                    WHERE person_id = %s
                    """,
                    (person_id,),
                )
                entity_rows = await cur.fetchall()

        assert role_row is not None
        assert role_row[0] is True
        assert role_row[1][0]["label"] == "At school or college"
        assert role_row[1][1]["label"] == "They seemed confident"
        assert role_row[1][2]["skipped"] is True

        assert coverage_row is not None
        coverage = coverage_row[0]
        assert coverage["place"] == 1
        assert coverage["era"] == 1
        assert coverage["relation"] == 1
        assert coverage["voice"] == 1

        assert entity_rows
        assert entity_rows[0][0] == "place"
        assert entity_rows[0][1] == "school or college"
        assert entity_rows[0][2]["source"] == "archetype_onboarding"

    async def test_complete_role_returns_409(
        self, client_with_db, async_db_pool
    ) -> None:
        _, role_id = await _create_friend_role(client_with_db, async_db_pool)
        async with async_db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE person_roles SET onboarding_complete = true WHERE id = %s",
                    (role_id,),
                )
            await conn.commit()

        resp = await client_with_db.get(
            "/api/v1/onboarding/archetype-questions",
            headers=auth_headers(),
            params={"role_id": role_id},
        )

        assert resp.status_code == 409
