"""Speaker-first retrieval + provenance on MomentResult (sub-project 2)."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from flashback.retrieval.schema import MomentResult
from tests.retrieval.conftest import insert_moment, insert_person, vector

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


def _moment_row(**overrides):
    row = {
        "id": uuid4(),
        "person_id": uuid4(),
        "title": "t",
        "narrative": "n",
        "time_anchor": None,
        "life_period_estimate": None,
        "sensory_details": None,
        "emotional_tone": None,
        "contributor_perspective": None,
        "created_at": datetime(2026, 6, 15, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_moment_result_carries_told_by():
    uid = uuid4()
    m = MomentResult.model_validate(
        _moment_row(told_by_user_id=uid, told_by_display_name="Ravi")
    )
    assert m.told_by_user_id == uid
    assert m.told_by_display_name == "Ravi"


def test_moment_result_told_by_defaults_none():
    m = MomentResult.model_validate(_moment_row())
    assert m.told_by_user_id is None
    assert m.told_by_display_name is None


@db_only
async def test_own_moment_outranks_equal_distance_other(async_db_pool, retrieval_service):
    """Equal raw distance → the current speaker's own moment ranks first (soft bias)."""
    person = await insert_person(async_db_pool, "Subj")
    me = uuid4()
    other = uuid4()
    # both embeddings identical to the query (FakeEmbedder default = vector(1,0))
    own = await insert_moment(
        async_db_pool, person, title="own", embedding=vector(1.0, 0.0),
        told_by_user_id=me,
    )
    await insert_moment(
        async_db_pool, person, title="other", embedding=vector(1.0, 0.0),
        told_by_user_id=other,
    )
    results = await retrieval_service.search_moments(
        "q", person, current_user_id=me
    )
    assert results[0].id == own


@db_only
async def test_strong_other_still_beats_weak_own(async_db_pool, retrieval_service):
    """A much closer cross-contributor moment still outranks a far own one (soft, not hard)."""
    person = await insert_person(async_db_pool, "Subj")
    me = uuid4()
    other = uuid4()
    # own is far from the query (1,0): embed (0,1) → cosine distance ~1.0
    await insert_moment(
        async_db_pool, person, title="own-far", embedding=vector(0.0, 1.0),
        told_by_user_id=me,
    )
    close_other = await insert_moment(
        async_db_pool, person, title="other-close", embedding=vector(1.0, 0.0),
        told_by_user_id=other,
    )
    results = await retrieval_service.search_moments(
        "q", person, current_user_id=me
    )
    assert results[0].id == close_other  # bias (0.1) can't overcome a ~1.0 gap


@db_only
async def test_no_current_user_is_pure_similarity(async_db_pool, retrieval_service):
    """current_user_id=None disables the bias → pure distance ordering."""
    person = await insert_person(async_db_pool, "Subj")
    a = uuid4()
    close = await insert_moment(
        async_db_pool, person, title="close", embedding=vector(1.0, 0.0),
        told_by_user_id=a,
    )
    far = await insert_moment(
        async_db_pool, person, title="far", embedding=vector(0.0, 1.0),
        told_by_user_id=a,
    )
    results = await retrieval_service.search_moments("q", person, current_user_id=None)
    assert [r.id for r in results] == [close, far]
