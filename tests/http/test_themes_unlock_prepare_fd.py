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


# ---------------------------------------------------------------------------
# next_step + slug-scoped answered-lookup (2026-07-22 flow-control fix)
# ---------------------------------------------------------------------------

from psycopg.types.json import Json  # noqa: E402


async def _insert_campaign(pool, *, slug, version, bank, state="published",
                           status="active"):
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO tribute_campaigns
                         (slug, display_name, archetype_bank_override,
                          state, status, version)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id::text""",
                    (slug, "Test Occasion", Json(bank), state, status, version),
                )
                return (await cur.fetchone())[0]


async def _insert_tribute(pool, *, person_id, theme_id, campaign_id, answers,
                          message=None):
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO tributes
                         (person_id, theme_id, campaign_id, status,
                          archetype_answers, message_text)
                       VALUES (%s, %s, %s, 'draft', %s, %s) RETURNING id::text""",
                    (person_id, theme_id, campaign_id, Json(answers), message),
                )
                return (await cur.fetchone())[0]


_BANK = [{"question": "How did you meet?", "options": ["School", "Work"]}]
_ANSWERED = [{"question_text": "How did you meet?", "option_label": "School"}]


async def test_next_step_archetype_when_nothing_answered(
    client_with_db, async_db_pool, llm_must_not_run
) -> None:
    person_id, theme_id = await _seed_person_with_theme(async_db_pool, "friend")
    await _insert_campaign(async_db_pool, slug="occ_a", version=1, bank=_BANK)
    resp = await client_with_db.post(
        f"/themes/{theme_id}/unlock_prepare",
        json={"person_id": person_id, "campaign": "occ_a"},
        headers=_HEADERS,
    )
    body = resp.json()
    assert body["archetype_complete"] is False
    assert body["next_step"] == "archetype"


async def test_next_step_message_survives_campaign_version_bump(
    client_with_db, async_db_pool, llm_must_not_run
) -> None:
    # Answers committed under campaign v1; the campaign is then edited (v1
    # superseded, v2 published, same slug). unlock_prepare resolves v2 but must
    # still find the v1 answers via SLUG -> archetype_complete, and since it's a
    # campaign with no message yet -> next_step 'message'.
    person_id, theme_id = await _seed_person_with_theme(async_db_pool, "friend")
    v1 = await _insert_campaign(async_db_pool, slug="occ_b", version=1,
                                bank=_BANK, state="published", status="superseded")
    await _insert_campaign(async_db_pool, slug="occ_b", version=2, bank=_BANK)
    await _insert_tribute(async_db_pool, person_id=person_id, theme_id=theme_id,
                          campaign_id=v1, answers=_ANSWERED, message=None)
    resp = await client_with_db.post(
        f"/themes/{theme_id}/unlock_prepare",
        json={"person_id": person_id, "campaign": "occ_b"},
        headers=_HEADERS,
    )
    body = resp.json()
    assert body["tribute_answered"], "answers should be found across the version bump"
    assert body["archetype_complete"] is True
    assert body["next_step"] == "message"


async def test_next_step_conversation_when_message_present(
    client_with_db, async_db_pool, llm_must_not_run
) -> None:
    person_id, theme_id = await _seed_person_with_theme(async_db_pool, "friend")
    cid = await _insert_campaign(async_db_pool, slug="occ_c", version=1, bank=_BANK)
    await _insert_tribute(async_db_pool, person_id=person_id, theme_id=theme_id,
                          campaign_id=cid, answers=_ANSWERED,
                          message="you're my person.")
    resp = await client_with_db.post(
        f"/themes/{theme_id}/unlock_prepare",
        json={"person_id": person_id, "campaign": "occ_c"},
        headers=_HEADERS,
    )
    body = resp.json()
    assert body["archetype_complete"] is True
    assert body["next_step"] == "conversation"  # archetype done + message present
