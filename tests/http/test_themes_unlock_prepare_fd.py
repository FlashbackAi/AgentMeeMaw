"""unlock_prepare resolves archetype banks from DB config (tribute CRM).

Chain under test (spec 2026-07-14 section 6.3): campaign bank override ->
relationship-profile bank -> LLM generation seeded with relationship +
occasion context. The FD regression pins today's behavior: the seeded
fathers_day_2026 campaign serves its 22-question bank whole, no LLM.
"""

from __future__ import annotations

import pytest

from flashback.http.routes import themes as themes_route
from flashback.themes.archetype_llm import ArchetypeQuestion
from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)

_HEADERS = {"X-Service-Token": "test-token"}


async def _seed_person_with_theme(pool, relationship: str) -> tuple[str, str]:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name, relationship) "
                    "VALUES ('Subject', %s) RETURNING id::text",
                    (relationship,),
                )
                person_id = (await cur.fetchone())[0]
                theme_id = await ensure_tribute_theme_async(
                    cur,
                    person_id=person_id,
                    slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION,
                )
    return person_id, str(theme_id)


@pytest.fixture
def llm_must_not_run(monkeypatch):
    async def boom(**kw):  # pragma: no cover
        raise AssertionError("generate_archetype_questions must not be called")

    monkeypatch.setattr(themes_route, "generate_archetype_questions", boom)


async def test_fd_campaign_serves_22_question_bank_whole(
    client_with_db, async_db_pool, llm_must_not_run
) -> None:
    person_id, theme_id = await _seed_person_with_theme(async_db_pool, "father")
    resp = await client_with_db.post(
        f"/themes/{theme_id}/unlock_prepare",
        json={"person_id": person_id, "campaign": "fathers_day_2026"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    questions = resp.json()["archetype_questions"]
    assert len(questions) == 22
    assert questions[0]["text"] == "Where did your father grow up?"
    assert len(questions[0]["options"]) == 4


async def test_friend_profile_bank_no_campaign(
    client_with_db, async_db_pool, llm_must_not_run
) -> None:
    person_id, theme_id = await _seed_person_with_theme(
        async_db_pool, "best friend"
    )
    resp = await client_with_db.post(
        f"/themes/{theme_id}/unlock_prepare",
        json={"person_id": person_id},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    questions = resp.json()["archetype_questions"]
    assert len(questions) == 10  # the seeded friend bank
    assert questions[0]["text"] == "How did they and the contributor first collide?"
    # relationship group cached on the person
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT relationship_group FROM persons WHERE id = %s",
                (person_id,),
            )
            assert (await cur.fetchone())[0] == "friend"


async def test_unmapped_relationship_falls_to_llm_with_context(
    client_with_db, async_db_pool, monkeypatch
) -> None:
    from flashback.tribute import relationships

    async def classify_other(settings, label):
        assert label == "colleague"
        return "other"

    monkeypatch.setattr(
        relationships, "classify_relationship_llm", classify_other
    )

    captured: dict = {}

    async def fake_generate(**kw):
        captured.update(kw)
        return [
            ArchetypeQuestion(
                question_id=f"q{i}",
                text=f"Generated {i}?",
                options=[
                    {"option_id": f"q{i}_o1", "label": "A"},
                    {"option_id": f"q{i}_o2", "label": "B"},
                ],
            )
            for i in range(1, 9)
        ]

    monkeypatch.setattr(
        themes_route, "generate_archetype_questions", fake_generate
    )
    person_id, theme_id = await _seed_person_with_theme(
        async_db_pool, "colleague"
    )
    resp = await client_with_db.post(
        f"/themes/{theme_id}/unlock_prepare",
        json={"person_id": person_id},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json()["archetype_questions"]) == 8
    # LLM generation was seeded with the raw relationship label.
    assert captured["subject_relationship"] == "colleague"
    assert captured["min_questions"] == 8 and captured["max_questions"] == 22
