from __future__ import annotations

import os
import socket
from urllib.parse import urlparse
from uuid import UUID

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


def _skip_all_answers() -> list[dict]:
    return [
        {"question_id": qid, "skipped": True}
        for qid in [
            "friend_meet",
            "friend_shared_place",
            "friend_usual_activity",
            "friend_kind",
            "friend_first_memory",
            "universal_what_you_call_them",
            "universal_their_work",
            "universal_their_place",
            "gt_region",
            "gt_birth_era",
        ]
    ]


async def _create_friend_person(client_with_db) -> str:
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
    return person_resp.json()["person_id"]


class TestArchetypeQuestions:
    async def test_returns_public_questions_without_implies(
        self, client_with_db, async_db_pool
    ) -> None:
        person_id = await _create_friend_person(client_with_db)

        resp = await client_with_db.get(
            "/api/v1/onboarding/archetype-questions",
            headers=auth_headers(),
            params={"person_id": person_id},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["relationship"] == "friend"
        assert body["archetype"] == "friend"
        # 5 relationship + 3 universal + 2 appended ground-truth questions.
        ids = [q["id"] for q in body["questions"]]
        assert len(ids) == 10
        assert ids[:3] == [
            "friend_meet",
            "friend_shared_place",
            "friend_usual_activity",
        ]
        assert "gt_region" in ids and "gt_birth_era" in ids
        assert "implies" not in body["questions"][0]["options"][0]
        # Multi-select contract: everything except the ground-truth pair.
        flags = {q["id"]: q["allow_multiple"] for q in body["questions"]}
        assert flags["friend_meet"] is True
        assert flags["gt_region"] is False
        assert flags["gt_birth_era"] is False


class TestArchetypeAnswers:
    async def test_persists_answers_and_coverage_without_seeding_entities(
        self, client_with_db, async_db_pool
    ) -> None:
        person_id = await _create_friend_person(client_with_db)

        resp = await client_with_db.post(
            "/api/v1/onboarding/archetype-answers",
            headers=auth_headers(),
            json={
                "person_id": person_id,
                "answers": [
                    {"question_id": "friend_meet", "option_id": "school"},
                    {
                        "question_id": "friend_shared_place",
                        "option_id": "calls",
                    },
                    {"question_id": "friend_usual_activity", "skipped": True},
                    {"question_id": "friend_kind", "skipped": True},
                    {"question_id": "friend_first_memory", "skipped": True},
                    # Universal questions must be present (route enforces every
                    # question exactly once); skip them so coverage is unchanged.
                    {"question_id": "universal_what_you_call_them", "skipped": True},
                    {"question_id": "universal_their_work", "skipped": True},
                    {"question_id": "universal_their_place", "skipped": True},
                    {"question_id": "gt_region", "option_id": "another_state"},
                    {"question_id": "gt_birth_era", "option_id": "era_50s_60s"},
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
                    FROM persons
                    WHERE id = %s
                    """,
                    (person_id,),
                )
                person_row = await cur.fetchone()
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

        assert person_row is not None
        assert person_row[0] is True
        assert person_row[1][0]["label"] == "Through school"
        assert person_row[1][1]["label"] == "On calls or messages"
        assert person_row[1][2]["skipped"] is True

        assert coverage_row is not None
        coverage = coverage_row[0]
        assert coverage["place"] == 1
        assert coverage["era"] == 1
        # 'Through school' and 'On calls or messages' both imply relation
        assert coverage["relation"] == 2
        assert coverage["voice"] == 1

        # Ground-truth onboarding answers land in persons.ground_truth
        # with provenance='onboarding' (invariant #26).
        async with async_db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT ground_truth FROM persons WHERE id = %s",
                    (person_id,),
                )
                gt_row = await cur.fetchone()
        ground_truth = gt_row[0]
        assert ground_truth["region"]["value"] == "Another part of the country"
        assert ground_truth["region"]["provenance"] == "onboarding"
        assert ground_truth["birth_era"]["value"] == "1950s or 60s"

        # Onboarding no longer seeds entities — the implied "school" place
        # is not persisted. Coverage deltas above are the only graph effect;
        # extraction mines the resulting conversation for real entities.
        assert entity_rows == []

    async def test_full_ten_question_submission_succeeds(
        self, client_with_db, async_db_pool
    ) -> None:
        # Production answers EVERY returned question (5 base + 3 universal + 2
        # ground-truth = 10). The request cap used to be 8, which 422'd every
        # full submission; it must accept the full set.
        person_id = await _create_friend_person(client_with_db)
        resp = await client_with_db.post(
            "/api/v1/onboarding/archetype-answers",
            headers=auth_headers(),
            json={
                "person_id": person_id,
                "answers": [
                    {"question_id": "friend_meet", "option_id": "school"},
                    {"question_id": "friend_shared_place", "option_id": "calls"},
                    {"question_id": "friend_usual_activity", "option_id": "talk"},
                    {"question_id": "friend_kind", "option_id": "funny"},
                    {"question_id": "friend_first_memory", "option_id": "laughed"},
                    {"question_id": "universal_what_you_call_them", "option_id": "by_name"},
                    {"question_id": "universal_their_work", "option_id": "trade"},
                    {"question_id": "universal_their_place", "option_id": "role_model"},
                    {"question_id": "gt_region", "option_id": "another_state"},
                    {"question_id": "gt_birth_era", "option_id": "era_50s_60s"},
                ],
            },
        )

        assert resp.status_code == 200, resp.text
        UUID(resp.json()["session_id"])

    async def test_multi_select_answers_merge_implies(
        self, client_with_db, async_db_pool
    ) -> None:
        person_id = await _create_friend_person(client_with_db)

        resp = await client_with_db.post(
            "/api/v1/onboarding/archetype-answers",
            headers=auth_headers(),
            json={
                "person_id": person_id,
                "answers": [
                    # "Through school" + "Through work": coverage unions to
                    # place/era/relation; conflicting life periods (school vs
                    # working years) are dropped. Onboarding seeds no entities.
                    {"question_id": "friend_meet", "option_ids": ["school", "work"]},
                    {"question_id": "friend_shared_place", "skipped": True},
                    {"question_id": "friend_usual_activity", "option_ids": ["talk", "eat"]},
                    {"question_id": "friend_kind", "skipped": True},
                    {"question_id": "friend_first_memory", "skipped": True},
                    {"question_id": "universal_what_you_call_them", "skipped": True},
                    {"question_id": "universal_their_work", "skipped": True},
                    {"question_id": "universal_their_place", "skipped": True},
                    # Legacy single shape still accepted on the GT pair.
                    {"question_id": "gt_region", "option_id": "another_state"},
                    {"question_id": "gt_birth_era", "skipped": True},
                ],
            },
        )

        assert resp.status_code == 200, resp.text

        async with async_db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT archetype_answers, coverage_state FROM persons WHERE id = %s",
                    (person_id,),
                )
                row = await cur.fetchone()
                await cur.execute(
                    "SELECT kind, name FROM entities WHERE person_id = %s ORDER BY name",
                    (person_id,),
                )
                entity_rows = await cur.fetchall()

        answers, coverage = row
        first = answers[0]
        assert first["option_ids"] == ["school", "work"]
        assert first["labels"] == ["Through school", "Through work"]
        # Legacy mirrors point at the first selection.
        assert first["option_id"] == "school"
        assert first["label"] == "Through school"

        # friend_meet merged block counts each dim once despite both chips
        # implying relation; friend_usual_activity adds voice+sensory+relation.
        assert coverage["place"] == 1
        assert coverage["era"] == 1
        assert coverage["relation"] == 2
        assert coverage["voice"] == 1
        assert coverage["sensory"] == 1

        # Coverage unions correctly, but no entities are seeded from the
        # implied place/organization.
        assert entity_rows == []

    async def test_multi_select_rejected_on_ground_truth_question(
        self, client_with_db, async_db_pool
    ) -> None:
        person_id = await _create_friend_person(client_with_db)
        answers = _skip_all_answers()
        answers[-2] = {
            "question_id": "gt_region",
            "option_ids": ["same_place", "abroad"],
        }
        resp = await client_with_db.post(
            "/api/v1/onboarding/archetype-answers",
            headers=auth_headers(),
            json={"person_id": person_id, "answers": answers},
        )
        assert resp.status_code == 422
        assert "single option" in resp.text

    async def test_skipped_answer_with_options_rejected(
        self, client_with_db, async_db_pool
    ) -> None:
        person_id = await _create_friend_person(client_with_db)
        answers = _skip_all_answers()
        answers[0] = {
            "question_id": "friend_meet",
            "option_ids": ["school"],
            "skipped": True,
        }
        resp = await client_with_db.post(
            "/api/v1/onboarding/archetype-answers",
            headers=auth_headers(),
            json={"person_id": person_id, "answers": answers},
        )
        assert resp.status_code == 422
        assert "skipped answer cannot" in resp.text

    async def test_complete_person_returns_409(
        self, client_with_db, async_db_pool
    ) -> None:
        person_id = await _create_friend_person(client_with_db)
        async with async_db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE persons SET onboarding_complete = true WHERE id = %s",
                    (person_id,),
                )
            await conn.commit()

        resp = await client_with_db.get(
            "/api/v1/onboarding/archetype-questions",
            headers=auth_headers(),
            params={"person_id": person_id},
        )

        assert resp.status_code == 409
