"""DB-gated test: SELECT_STEADY_CANDIDATES content-scoping (SP4).

Verifies the eligibility filter added to SELECT_STEADY_CANDIDATES:
  - public questions are visible to all callers
  - personal questions (default scope) are visible to the author and to
    shared rows (told_by=NULL), but NOT to other contributors
  - private questions are only visible when told_by IS NOT DISTINCT FROM
    current_user_id (NULL-safe equality)
  - untagged rows (no 'scope' key in attributes) default to 'personal'

Also verifies that SELECT_UNANSWERED_COVERAGE_TAP accepts the
current_user_id param without error (coverage taps are global /
person_id IS NULL today, so the clause is a no-op, but the param must
be accepted for spec fidelity).

Requires TEST_DATABASE_URL — skipped when absent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from psycopg.types.json import Json

from flashback.db.connection import make_async_pool
from flashback.phase_gate.queries import SELECT_STEADY_CANDIDATES, SELECT_UNANSWERED_COVERAGE_TAP

pytestmark = pytest.mark.asyncio

PRODUCER_SOURCES = [
    "underdeveloped_entity",
    "dropped_reference",
    "thread_deepen",
    "life_period_gap",
    "universal_dimension",
]

DAUGHTER_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SON_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_db_pool(schema_applied: str):
    pool = make_async_pool(schema_applied, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM edges")
                await cur.execute("DELETE FROM questions WHERE source <> 'coverage_tap'")
                await cur.execute("DELETE FROM persons")
            await conn.commit()
        await pool.close()


async def _insert_person(pool) -> uuid.UUID:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO persons (name) VALUES (%s) RETURNING id",
                ("Test Subject",),
            )
            (person_id,) = await cur.fetchone()
        await conn.commit()
    return person_id


async def _insert_question(
    pool,
    person_id: uuid.UUID,
    *,
    text: str,
    source: str = "dropped_reference",
    scope: str | None = None,
    told_by_user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a question with optional scope and told_by_user_id."""
    attributes: dict = {"dropped_phrase": "the porch", "themes": []}
    if scope is not None:
        attributes["scope"] = scope

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO questions
                    (person_id, text, source, attributes, told_by_user_id,
                     status, created_at)
                VALUES (%s, %s, %s, %s, %s, 'active', %s)
                RETURNING id
                """,
                (
                    person_id,
                    text,
                    source,
                    Json(attributes),
                    told_by_user_id,
                    datetime.now(timezone.utc),
                ),
            )
            (question_id,) = await cur.fetchone()
        await conn.commit()
    return question_id


async def _run_query(
    pool,
    person_id: uuid.UUID,
    *,
    current_user_id: uuid.UUID | None,
) -> set[uuid.UUID]:
    """Execute SELECT_STEADY_CANDIDATES and return the set of visible question ids."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                SELECT_STEADY_CANDIDATES,
                {
                    "person_id": person_id,
                    "recent_ids": [],
                    "sources": PRODUCER_SOURCES,
                    "exclude_skipped": False,
                    "current_user_id": current_user_id,
                },
            )
            rows = await cur.fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_public_question_visible_to_all(async_db_pool):
    """A public question is visible regardless of caller identity."""
    person_id = await _insert_person(async_db_pool)
    q_public = await _insert_question(
        async_db_pool, person_id,
        text="Public question?",
        source="dropped_reference",
        scope="public",
        told_by_user_id=DAUGHTER_ID,
    )

    # visible to daughter
    ids_daughter = await _run_query(async_db_pool, person_id, current_user_id=DAUGHTER_ID)
    assert q_public in ids_daughter, "public question should be visible to its author"

    # visible to creator era (None)
    ids_creator = await _run_query(async_db_pool, person_id, current_user_id=None)
    assert q_public in ids_creator, "public question should be visible to creator era"


async def test_personal_own_visible_to_author_not_creator(async_db_pool):
    """Personal scope + told_by=daughter → visible to daughter, NOT to creator(None)."""
    person_id = await _insert_person(async_db_pool)
    q_personal_own = await _insert_question(
        async_db_pool, person_id,
        text="Personal own question?",
        source="underdeveloped_entity",
        scope="personal",
        told_by_user_id=DAUGHTER_ID,
    )

    ids_daughter = await _run_query(async_db_pool, person_id, current_user_id=DAUGHTER_ID)
    assert q_personal_own in ids_daughter, "personal own question visible to author"

    ids_creator = await _run_query(async_db_pool, person_id, current_user_id=None)
    assert q_personal_own not in ids_creator, "personal own question NOT visible to creator"


async def test_personal_shared_visible_to_both(async_db_pool):
    """Personal scope + told_by=NULL (shared) → visible to both daughter and creator."""
    person_id = await _insert_person(async_db_pool)
    q_shared = await _insert_question(
        async_db_pool, person_id,
        text="Shared personal question?",
        source="life_period_gap",
        scope="personal",
        told_by_user_id=None,
    )

    ids_daughter = await _run_query(async_db_pool, person_id, current_user_id=DAUGHTER_ID)
    assert q_shared in ids_daughter, "shared personal question visible to daughter"

    ids_creator = await _run_query(async_db_pool, person_id, current_user_id=None)
    assert q_shared in ids_creator, "shared personal question visible to creator"


async def test_private_own_visible_only_to_exact_match(async_db_pool):
    """Private scope + told_by=daughter → visible to daughter only (NULL-safe match)."""
    person_id = await _insert_person(async_db_pool)
    q_private = await _insert_question(
        async_db_pool, person_id,
        text="Private own question?",
        source="thread_deepen",
        scope="private",
        told_by_user_id=DAUGHTER_ID,
    )

    ids_daughter = await _run_query(async_db_pool, person_id, current_user_id=DAUGHTER_ID)
    assert q_private in ids_daughter, "private own question visible to author"

    ids_creator = await _run_query(async_db_pool, person_id, current_user_id=None)
    assert q_private not in ids_creator, "private own question NOT visible to creator"


async def test_untagged_defaults_to_personal(async_db_pool):
    """No 'scope' key in attributes → behaves as personal (visible to author, not creator)."""
    person_id = await _insert_person(async_db_pool)
    q_untagged = await _insert_question(
        async_db_pool, person_id,
        text="Untagged question?",
        source="universal_dimension",
        scope=None,                # no scope key at all
        told_by_user_id=DAUGHTER_ID,
    )

    ids_daughter = await _run_query(async_db_pool, person_id, current_user_id=DAUGHTER_ID)
    assert q_untagged in ids_daughter, "untagged question visible to author (defaults to personal)"

    ids_creator = await _run_query(async_db_pool, person_id, current_user_id=None)
    assert q_untagged not in ids_creator, "untagged question NOT visible to creator (defaults to personal)"


async def test_personal_question_not_visible_to_different_contributor(async_db_pool):
    """Personal scope + told_by=DAUGHTER is NOT visible to SON (a different contributor)."""
    person_id = await _insert_person(async_db_pool)
    q_personal = await _insert_question(
        async_db_pool, person_id,
        text="Daughter personal question?",
        source="dropped_reference",
        scope="personal",
        told_by_user_id=DAUGHTER_ID,
    )

    # different contributor cannot see it
    ids_son = await _run_query(async_db_pool, person_id, current_user_id=SON_ID)
    assert q_personal not in ids_son, "personal question must NOT be visible to a different contributor"

    # sanity: the author herself can still see it
    ids_daughter = await _run_query(async_db_pool, person_id, current_user_id=DAUGHTER_ID)
    assert q_personal in ids_daughter, "personal question must still be visible to its author"


async def test_private_null_told_by_visible_only_to_creator_session(async_db_pool):
    """Private scope + told_by=NULL (creator era) is visible to current_user_id=None only."""
    person_id = await _insert_person(async_db_pool)
    q_private_creator = await _insert_question(
        async_db_pool, person_id,
        text="Creator era private question?",
        source="dropped_reference",
        scope="private",
        told_by_user_id=None,
    )

    # creator session (None) can see it
    ids_creator = await _run_query(async_db_pool, person_id, current_user_id=None)
    assert q_private_creator in ids_creator, "private NULL told_by must be visible to creator session"

    # any collaborator cannot see it
    ids_son = await _run_query(async_db_pool, person_id, current_user_id=SON_ID)
    assert q_private_creator not in ids_son, "private NULL told_by must NOT be visible to a collaborator"


async def test_coverage_tap_query_accepts_current_user_id(async_db_pool):
    """SELECT_UNANSWERED_COVERAGE_TAP accepts current_user_id without error.

    Coverage taps are global (person_id IS NULL, told_by_user_id NULL, scope
    will be seeded as 'public'), so the clause is effectively a no-op today.
    This test confirms the param binding is wired correctly.
    """
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                SELECT_UNANSWERED_COVERAGE_TAP,
                {
                    "dimension": "era",
                    "recent_ids": [],
                    "person_id": str(uuid.uuid4()),
                    "current_user_id": None,
                },
            )
            await cur.fetchall()  # result may be empty — what matters is no error
