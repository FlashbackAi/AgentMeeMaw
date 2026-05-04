from __future__ import annotations

from tests.retrieval.conftest import (
    MODEL,
    insert_moment,
    insert_person,
    recent,
    vector,
)


async def test_search_returns_only_requested_person(async_db_pool, retrieval_service):
    person_a = await insert_person(async_db_pool, "A")
    person_b = await insert_person(async_db_pool, "B")
    for idx in range(5):
        await insert_moment(
            async_db_pool,
            person_a,
            title=f"A {idx}",
            embedding=vector(1.0, idx / 100),
        )
    for idx in range(3):
        await insert_moment(
            async_db_pool,
            person_b,
            title=f"B {idx}",
            embedding=vector(1.0, idx / 100),
        )

    results = await retrieval_service.search_moments("porch", person_a)

    assert len(results) == 5
    assert {result.person_id for result in results} == {person_a}


async def test_embedding_failure_returns_empty(async_db_pool, retrieval_service, fake_embedder):
    person_id = await insert_person(async_db_pool)
    await insert_moment(async_db_pool, person_id, embedding=vector())
    fake_embedder.value = None

    assert await retrieval_service.search_moments("porch", person_id) == []


async def test_limit_is_applied(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    for idx in range(5):
        await insert_moment(async_db_pool, person_id, embedding=vector(1.0, idx / 10))

    assert len(await retrieval_service.search_moments("porch", person_id, limit=3)) == 3


async def test_limit_none_uses_default_limit(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    for idx in range(12):
        await insert_moment(async_db_pool, person_id, embedding=vector(1.0, idx / 100))

    assert len(await retrieval_service.search_moments("porch", person_id)) == 10


async def test_limit_is_clamped_to_max(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    for idx in range(55):
        await insert_moment(async_db_pool, person_id, embedding=vector(1.0, idx / 1000))

    assert len(await retrieval_service.search_moments("porch", person_id, limit=999)) == 50


async def test_status_model_and_null_embedding_are_filtered(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    active_id = await insert_moment(
        async_db_pool,
        person_id,
        title="active",
        embedding=vector(),
    )
    await insert_moment(
        async_db_pool,
        person_id,
        title="superseded",
        status="superseded",
        embedding=vector(),
    )
    await insert_moment(
        async_db_pool,
        person_id,
        title="wrong model",
        embedding=vector(),
        model="different-model",
    )
    await insert_moment(async_db_pool, person_id, title="no vector", embedding=None)

    results = await retrieval_service.search_moments("porch", person_id)

    assert [result.id for result in results] == [active_id]


async def test_results_ordered_by_similarity(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    closest = await insert_moment(
        async_db_pool,
        person_id,
        title="closest",
        embedding=vector(1.0, 0.0),
        created_at=recent(1),
    )
    middle = await insert_moment(
        async_db_pool,
        person_id,
        title="middle",
        embedding=vector(0.8, 0.2),
        created_at=recent(2),
    )
    farthest = await insert_moment(
        async_db_pool,
        person_id,
        title="farthest",
        embedding=vector(0.0, 1.0),
        created_at=recent(3),
    )

    results = await retrieval_service.search_moments("porch", person_id)

    assert [result.id for result in results] == [closest, middle, farthest]
    assert results[0].similarity_score is not None


async def test_search_uses_configured_model_version(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    match = await insert_moment(async_db_pool, person_id, embedding=vector(), model=MODEL)
    await insert_moment(
        async_db_pool,
        person_id,
        title="old version",
        embedding=vector(),
        version="2024-01-01",
    )

    results = await retrieval_service.search_moments("porch", person_id)

    assert [result.id for result in results] == [match]
