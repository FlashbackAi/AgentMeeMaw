"""
Shared pytest fixtures.

DB-touching tests need a real Postgres + pgvector. We do not spin one
up inline (pgvector binaries are not bundled with pip-installed
postgres). Instead, the user supplies ``TEST_DATABASE_URL`` pointing
at a test database. If unset, DB-dependent tests are skipped.

Each DB test gets a freshly migrated database via the
``schema_applied`` session fixture, plus its own transactional
``db_pool`` fixture that rolls back at teardown.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "migrations"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set; skipping DB-touching tests. "
            "Set it to a Postgres instance with pgvector installed."
        )
    return url


@pytest.fixture(scope="session")
def schema_applied(test_database_url: str) -> str:
    """
    Drop any existing public objects, then apply 0001 + 0002 migrations.

    Returns the same URL so dependent fixtures can chain. Schema is
    applied once per session to keep the test loop fast.
    """
    import psycopg

    up_files = sorted(MIGRATIONS_DIR.glob("*.up.sql"))
    if not up_files:
        pytest.fail(f"no migrations found under {MIGRATIONS_DIR}")

    with psycopg.connect(test_database_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")

    for path in up_files:
        sql = path.read_text(encoding="utf-8")
        with psycopg.connect(test_database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)

    return test_database_url


@pytest.fixture
def db_pool(schema_applied: str):
    """A small psycopg pool with the pgvector adapter registered."""
    from flashback.db.connection import make_pool

    pool = make_pool(schema_applied, min_size=1, max_size=2)
    try:
        yield pool
    finally:
        pool.close()


@pytest.fixture
def make_person(db_pool):
    """Insert a person and return its id, for tests that need a parent row."""

    def _make(name: str = "Test Subject") -> str:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO persons (name) VALUES (%s) RETURNING id",
                    (name,),
                )
                (person_id,) = cur.fetchone()
                conn.commit()
        return str(person_id)

    return _make


@pytest.fixture
def reset_questions(db_pool):
    """
    The seed migration left 15 starter_anchor rows. Some tests want a
    clean slate; this fixture truncates the questions table for them.
    """

    def _reset() -> None:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM questions")
                conn.commit()

    return _reset
