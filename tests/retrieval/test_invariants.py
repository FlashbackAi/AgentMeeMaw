from __future__ import annotations

import inspect
from uuid import uuid4

from flashback.retrieval import RetrievalService
from tests.retrieval.conftest import (
    insert_entity,
    insert_moment,
    insert_person,
    insert_question,
    insert_thread,
    vector,
)


def test_public_methods_are_person_or_session_scoped() -> None:
    for name, method in inspect.getmembers(RetrievalService, inspect.iscoroutinefunction):
        if name.startswith("_") or name == "embed_query":
            continue
        params = inspect.signature(method).parameters
        assert "person_id" in params or "session_id" in params, name


async def test_active_view_drift_detector(async_db_pool, retrieval_service):
    person_id = await insert_person(async_db_pool)
    active_moment = await insert_moment(async_db_pool, person_id, embedding=vector())
    await insert_moment(
        async_db_pool,
        person_id,
        status="superseded",
        embedding=vector(),
    )
    active_entity = await insert_entity(async_db_pool, person_id, name="Active")
    await insert_entity(async_db_pool, person_id, name="Merged", status="merged")
    active_thread = await insert_thread(async_db_pool, person_id, name="Active")
    await insert_thread(async_db_pool, person_id, name="Archived", status="archived")
    active_question = await insert_question(async_db_pool, person_id, status="active")
    await insert_question(async_db_pool, person_id, status="asked")

    assert [m.id for m in await retrieval_service.search_moments("porch", person_id)] == [
        active_moment
    ]
    assert [e.id for e in await retrieval_service.get_entities(person_id)] == [
        active_entity
    ]
    assert [t.id for t in await retrieval_service.get_threads(person_id)] == [
        active_thread
    ]
    dropped = await retrieval_service.get_dropped_phrases_for_session(uuid4(), person_id)
    assert [q.question_id for q in dropped] == [active_question]
