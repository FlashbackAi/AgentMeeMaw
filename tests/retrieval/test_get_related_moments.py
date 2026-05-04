from __future__ import annotations

from tests.retrieval.conftest import (
    insert_edge,
    insert_entity,
    insert_moment,
    insert_person,
    vector,
)


async def test_get_related_moments_returns_involved_moments(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    entity_id = await insert_entity(async_db_pool, person_id, name="Porch")
    moment_id = await insert_moment(async_db_pool, person_id, embedding=vector())
    await insert_edge(
        async_db_pool,
        from_kind="moment",
        from_id=moment_id,
        to_kind="entity",
        to_id=entity_id,
        edge_type="involves",
    )

    results = await retrieval_service.get_related_moments(entity_id, person_id)

    assert [result.id for result in results] == [moment_id]


async def test_get_related_moments_enforces_person_scope(async_db_pool, retrieval_service):
    person_a = await insert_person(async_db_pool, "A")
    person_b = await insert_person(async_db_pool, "B")
    entity_id = await insert_entity(async_db_pool, person_a, name="Porch")
    moment_id = await insert_moment(async_db_pool, person_a, embedding=vector())
    await insert_edge(
        async_db_pool,
        from_kind="moment",
        from_id=moment_id,
        to_kind="entity",
        to_id=entity_id,
        edge_type="involves",
    )

    assert await retrieval_service.get_related_moments(entity_id, person_b) == []


async def test_get_related_moments_skips_archived_edges(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    entity_id = await insert_entity(async_db_pool, person_id)
    moment_id = await insert_moment(async_db_pool, person_id, embedding=vector())
    await insert_edge(
        async_db_pool,
        from_kind="moment",
        from_id=moment_id,
        to_kind="entity",
        to_id=entity_id,
        edge_type="involves",
        status="archived",
    )

    assert await retrieval_service.get_related_moments(entity_id, person_id) == []


async def test_get_related_moments_skips_superseded_moments(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    entity_id = await insert_entity(async_db_pool, person_id)
    moment_id = await insert_moment(
        async_db_pool,
        person_id,
        status="superseded",
        embedding=vector(),
    )
    await insert_edge(
        async_db_pool,
        from_kind="moment",
        from_id=moment_id,
        to_kind="entity",
        to_id=entity_id,
        edge_type="involves",
    )

    assert await retrieval_service.get_related_moments(entity_id, person_id) == []
