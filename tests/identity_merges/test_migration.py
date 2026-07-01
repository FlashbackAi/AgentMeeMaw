import os

import psycopg
import pytest

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
def test_provenance_columns_exist():
    conn = psycopg.connect(_DB, autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'identity_merge_suggestions'
               AND column_name IN ('source_told_by_user_id', 'target_told_by_user_id')
            """
        )
        cols = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    assert cols == {"source_told_by_user_id", "target_told_by_user_id"}
