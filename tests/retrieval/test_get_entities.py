from __future__ import annotations

from tests.retrieval.conftest import insert_entity, insert_person


async def test_get_entities_returns_all_for_person(async_db_pool, retrieval_service):
    person_a = await insert_person(async_db_pool, "A")
    person_b = await insert_person(async_db_pool, "B")
    await insert_entity(async_db_pool, person_a, kind="person", name="Maya")
    await insert_entity(async_db_pool, person_a, kind="place", name="Porch")
    await insert_entity(async_db_pool, person_b, kind="object", name="Lamp")

    results = await retrieval_service.get_entities(person_a)

    assert {result.name for result in results} == {"Maya", "Porch"}
    assert {result.person_id for result in results} == {person_a}


async def test_get_entities_kind_filter(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    await insert_entity(async_db_pool, person_id, kind="person", name="Maya")
    await insert_entity(async_db_pool, person_id, kind="place", name="Porch")

    results = await retrieval_service.get_entities(person_id, kind="place")

    assert [result.kind for result in results] == ["place"]
    assert [result.name for result in results] == ["Porch"]


async def test_get_entities_excludes_merged(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    active = await insert_entity(async_db_pool, person_id, name="Active")
    await insert_entity(async_db_pool, person_id, name="Merged", status="merged")

    results = await retrieval_service.get_entities(person_id)

    assert [result.id for result in results] == [active]
