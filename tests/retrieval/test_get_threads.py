from __future__ import annotations

from tests.retrieval.conftest import insert_person, insert_thread, recent


async def test_get_threads_returns_active_ordered_desc(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    older = await insert_thread(async_db_pool, person_id, name="Older", created_at=recent(1))
    newer = await insert_thread(async_db_pool, person_id, name="Newer", created_at=recent(2))

    results = await retrieval_service.get_threads(person_id)

    assert [result.id for result in results] == [newer, older]


async def test_get_threads_enforces_person_scope(async_db_pool, retrieval_service):
    person_a = await insert_person(async_db_pool, "A")
    person_b = await insert_person(async_db_pool, "B")
    thread_a = await insert_thread(async_db_pool, person_a, name="A")
    await insert_thread(async_db_pool, person_b, name="B")

    results = await retrieval_service.get_threads(person_a)

    assert [result.id for result in results] == [thread_a]


async def test_get_threads_excludes_archived(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    active = await insert_thread(async_db_pool, person_id, name="Active")
    await insert_thread(async_db_pool, person_id, name="Archived", status="archived")

    results = await retrieval_service.get_threads(person_id)

    assert [result.id for result in results] == [active]


async def test_get_threads_summary_returns_threads(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    thread_id = await insert_thread(async_db_pool, person_id)

    results = await retrieval_service.get_threads_summary(person_id)

    assert [result.id for result in results] == [thread_id]
