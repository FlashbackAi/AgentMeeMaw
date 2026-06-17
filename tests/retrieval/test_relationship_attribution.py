"""Relationship-aware attribution via collaborator_onboarding JOIN (sub-project 3 Task 6)."""

import os
from uuid import uuid4

import pytest

from tests.retrieval.conftest import insert_moment, insert_person, vector

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
async def test_told_by_relationship_populated_from_onboarding(async_db_pool, retrieval_service):
    """When a collaborator_onboarding row exists for the moment's told_by_user_id,
    told_by_relationship is populated from voice_anchor_text."""
    person = await insert_person(async_db_pool, "Subj")
    contributor = uuid4()

    await insert_moment(
        async_db_pool,
        person,
        title="Halwa lesson",
        embedding=vector(1.0, 0.0),
        told_by_user_id=contributor,
    )

    # Insert a collaborator_onboarding row for this contributor
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO collaborator_onboarding
                    (person_id, user_id, voice_anchor_text, status)
                VALUES (%s, %s, %s, 'active')
                """,
                (person, contributor, "her brother"),
            )
            await conn.commit()

    try:
        results = await retrieval_service.search_moments(
            "q", person, current_user_id=uuid4()
        )
        assert len(results) == 1
        assert results[0].told_by_user_id == contributor
        assert results[0].told_by_relationship == "her brother"
    finally:
        async with async_db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM collaborator_onboarding WHERE person_id = %s",
                    (person,),
                )
                await conn.commit()


@db_only
async def test_told_by_relationship_null_when_no_onboarding_row(async_db_pool, retrieval_service):
    """When no collaborator_onboarding row exists, told_by_relationship is NULL."""
    person = await insert_person(async_db_pool, "Subj")
    contributor = uuid4()

    await insert_moment(
        async_db_pool,
        person,
        title="Old memory",
        embedding=vector(1.0, 0.0),
        told_by_user_id=contributor,
    )

    results = await retrieval_service.search_moments(
        "q", person, current_user_id=uuid4()
    )
    assert len(results) == 1
    assert results[0].told_by_user_id == contributor
    assert results[0].told_by_relationship is None
