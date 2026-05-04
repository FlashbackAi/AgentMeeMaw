from __future__ import annotations

from uuid import uuid4

from tests.retrieval.conftest import insert_person, insert_question


async def test_get_dropped_phrases_returns_active_person_questions(async_db_pool, retrieval_service):
    person_a = await insert_person(async_db_pool, "A")
    person_b = await insert_person(async_db_pool, "B")
    q1 = await insert_question(
        async_db_pool,
        person_a,
        text="Who was Uncle Ray?",
        attributes={"dropped_phrase": "Uncle Ray", "themes": []},
    )
    q2 = await insert_question(
        async_db_pool,
        person_a,
        text="What was the blue shop?",
        attributes={"dropped_phrase": "blue shop", "themes": []},
    )
    await insert_question(
        async_db_pool,
        person_b,
        attributes={"dropped_phrase": "other", "themes": []},
    )

    results = await retrieval_service.get_dropped_phrases_for_session(uuid4(), person_a)

    assert {result.question_id for result in results} == {q1, q2}
    assert {result.dropped_phrase for result in results} == {"Uncle Ray", "blue shop"}


async def test_get_dropped_phrases_excludes_asked_and_archived(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    active = await insert_question(async_db_pool, person_id, status="active")
    await insert_question(async_db_pool, person_id, status="asked")
    await insert_question(async_db_pool, person_id, status="archived")

    results = await retrieval_service.get_dropped_phrases_for_session(uuid4(), person_id)

    assert [result.question_id for result in results] == [active]
