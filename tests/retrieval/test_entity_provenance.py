"""Cross-contributor name recognition: entity provenance resolved via the
collaborator_onboarding JOIN in get_entities_by_ids."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from psycopg.types.json import Json

from tests.retrieval.conftest import insert_person

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def test_entity_result_provenance_defaults_none():
    from flashback.retrieval.schema import EntityResult

    e = EntityResult(
        id=uuid4(),
        person_id=uuid4(),
        kind="person",
        name="X",
        description=None,
        aliases=[],
        attributes={},
        created_at=datetime.now(timezone.utc),
    )
    assert e.told_by_user_id is None
    assert e.told_by_display_name is None
    assert e.told_by_relationship is None


async def _insert_entity(pool, person_id, *, name, told_by):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO entities
                    (person_id, kind, name, description, aliases, attributes,
                     status, told_by_user_id)
                VALUES (%s, 'person', %s, 'desc', %s, %s, 'active', %s)
                RETURNING id
                """,
                (person_id, name, [], Json({}), told_by),
            )
            (eid,) = await cur.fetchone()
            await conn.commit()
    return eid


@db_only
async def test_collaborator_entity_resolves_name_and_relationship(
    async_db_pool, retrieval_service
):
    person = await insert_person(async_db_pool, "Subj")
    ravi = uuid4()
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO collaborator_onboarding
                    (person_id, user_id, voice_anchor_text, voice_anchored_at,
                     display_name, status)
                VALUES (%s, %s, 'his son', now(), 'Ravi', 'active')
                """,
                (person, ravi),
            )
            await conn.commit()
    eid = await _insert_entity(async_db_pool, person, name="Priya", told_by=ravi)

    [ent] = await retrieval_service.get_entities_by_ids(person, [eid])
    assert ent.told_by_user_id == ravi
    assert ent.told_by_display_name == "Ravi"
    assert ent.told_by_relationship == "his son"


@db_only
async def test_null_told_by_entity_has_no_provenance(async_db_pool, retrieval_service):
    person = await insert_person(async_db_pool, "Subj")
    eid = await _insert_entity(async_db_pool, person, name="Comet", told_by=None)

    [ent] = await retrieval_service.get_entities_by_ids(person, [eid])
    assert ent.told_by_user_id is None
    assert ent.told_by_display_name is None
    assert ent.told_by_relationship is None


@db_only
async def test_told_by_without_onboarding_row_has_no_name(
    async_db_pool, retrieval_service
):
    person = await insert_person(async_db_pool, "Subj")
    orphan = uuid4()  # a user_id with no collaborator_onboarding row
    eid = await _insert_entity(async_db_pool, person, name="Anuj", told_by=orphan)

    [ent] = await retrieval_service.get_entities_by_ids(person, [eid])
    assert ent.told_by_user_id == orphan
    assert ent.told_by_display_name is None
    assert ent.told_by_relationship is None
