from __future__ import annotations

from tests.retrieval.conftest import (
    insert_edge,
    insert_entity,
    insert_person,
    insert_thread,
)


async def test_get_threads_for_entity_returns_evidenced_threads(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    entity_id = await insert_entity(async_db_pool, person_id)
    thread_id = await insert_thread(async_db_pool, person_id)
    await insert_edge(
        async_db_pool,
        from_kind="entity",
        from_id=entity_id,
        to_kind="thread",
        to_id=thread_id,
        edge_type="evidences",
    )

    results = await retrieval_service.get_threads_for_entity(entity_id, person_id)

    assert [result.id for result in results] == [thread_id]


async def test_get_threads_for_entity_ignores_other_edge_types(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    entity_id = await insert_entity(async_db_pool, person_id)
    thread_id = await insert_thread(async_db_pool, person_id)
    await insert_edge(
        async_db_pool,
        from_kind="entity",
        from_id=entity_id,
        to_kind="thread",
        to_id=thread_id,
        edge_type="involves",
    )

    assert await retrieval_service.get_threads_for_entity(entity_id, person_id) == []
