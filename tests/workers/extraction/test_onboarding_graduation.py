"""DB-gated tests: apply_collaborator_onboarding_extraction helper.

Tests exercise the module-level helper directly with a sync cursor so we
don't have to drive the full Extraction Worker pipeline.

Fixtures used:
  db_pool  — from tests/conftest.py (requires TEST_DATABASE_URL)
  make_person — from tests/conftest.py
"""

from __future__ import annotations

import uuid

import pytest

from flashback.workers.extraction.worker import apply_collaborator_onboarding_extraction
from flashback.collaborator_onboarding.queries import GET_ONBOARDING_STATE_SQL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_row(cur, person_id, user_id, voice_anchor=None):
    if voice_anchor is None:
        cur.execute(
            "INSERT INTO collaborator_onboarding (person_id, user_id, voice_anchor_text, voice_anchored_at)"
            " VALUES (%s, %s, NULL, NULL)",
            (str(person_id), str(user_id)),
        )
    else:
        cur.execute(
            "INSERT INTO collaborator_onboarding (person_id, user_id, voice_anchor_text, voice_anchored_at)"
            " VALUES (%s, %s, %s, now())",
            (str(person_id), str(user_id), voice_anchor),
        )


def _insert_moment(cur, person_id, user_id):
    mid = uuid.uuid4()
    cur.execute(
        "INSERT INTO moments (id, person_id, title, narrative, told_by_user_id) VALUES (%s,%s,'m','n',%s)",
        (str(mid), str(person_id), str(user_id)),
    )
    return mid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_memory_plus_inferred_relationship_graduates(db_pool, make_person):
    """First moment + inferred relationship → phase flips to 'active'."""
    person_id = make_person("Grad A")
    user_id = uuid.uuid4()

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                _seed_row(cur, person_id, user_id, voice_anchor=None)  # no connection yet
                mid = _insert_moment(cur, person_id, user_id)
                apply_collaborator_onboarding_extraction(
                    cur,
                    person_id=str(person_id),
                    user_id=str(user_id),
                    moment_ids=[str(mid)],
                    contributor_relationship="his daughter",
                )
                cur.execute(
                    GET_ONBOARDING_STATE_SQL,
                    {"person_id": person_id, "user_id": user_id},
                )
                phase, has_memory, has_connection, _ = cur.fetchone()

    assert phase == "active" and has_memory and has_connection


def test_no_clobber_existing_anchor(db_pool, make_person):
    """When a voice anchor already exists it must not be overwritten."""
    person_id = make_person("Grad B")
    user_id = uuid.uuid4()

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                _seed_row(cur, person_id, user_id, voice_anchor="his daughter")
                mid = _insert_moment(cur, person_id, user_id)
                apply_collaborator_onboarding_extraction(
                    cur,
                    person_id=str(person_id),
                    user_id=str(user_id),
                    moment_ids=[str(mid)],
                    contributor_relationship="some other phrase",
                )
                cur.execute(
                    "SELECT voice_anchor_text FROM collaborator_onboarding"
                    " WHERE person_id=%s AND user_id=%s",
                    (str(person_id), str(user_id)),
                )
                row = cur.fetchone()

    assert row[0] == "his daughter"  # unchanged


def test_creator_era_null_user_is_noop(db_pool, make_person):
    """Calling with user_id=None (creator era) must not raise."""
    person_id = make_person("Grad C")
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                apply_collaborator_onboarding_extraction(
                    cur,
                    person_id=str(person_id),
                    user_id=None,
                    moment_ids=[str(uuid.uuid4())],
                    contributor_relationship=None,
                )  # must not raise
