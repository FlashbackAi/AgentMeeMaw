"""Migration 0036 — moments.storybook_collections + GIN index.

Verifies the tag column exists as a nullable TEXT[] and the GIN index is
created. The migration deliberately does NOT touch the active_moments view
(the repository reads the base table with an explicit status filter), so the
column stays trivially droppable in the down migration.
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")


@pytest.fixture
def db_conn(schema_applied: str):
    with psycopg.connect(schema_applied) as conn:
        yield conn


def test_moments_has_nullable_text_array_column(db_conn) -> None:
    row = db_conn.execute(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'moments' AND column_name = 'storybook_collections'"
    ).fetchone()
    assert row is not None
    assert row[0] == "ARRAY"
    assert row[1] == "YES"  # NULL = never tagged; distinct from '{}'


def test_gin_index_exists(db_conn) -> None:
    row = db_conn.execute(
        "SELECT indexdef FROM pg_indexes "
        "WHERE indexname = 'idx_moments_storybook_collections'"
    ).fetchone()
    assert row is not None
    assert "gin" in row[0].lower()


def test_migration_leaves_active_moments_untouched(db_conn) -> None:
    # The view is intentionally NOT recreated; the repository reads the base
    # table. The column must therefore be absent from the SELECT * view.
    cols = {
        r[0]
        for r in db_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'active_moments'"
        ).fetchall()
    }
    assert "storybook_collections" not in cols
