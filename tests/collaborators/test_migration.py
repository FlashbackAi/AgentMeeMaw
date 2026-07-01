import os

import psycopg
import pytest

_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(not _DB, reason="TEST_DATABASE_URL not set")


@db_only
def test_removed_status_allowed_on_moments_and_entities():
    conn = psycopg.connect(_DB, autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO persons (name) VALUES ('Subj') RETURNING id::text")
        pid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO moments (person_id, title, narrative, status) "
            "VALUES (%s, 'M', 'n', 'removed') RETURNING id::text",
            (pid,),
        )
        assert cur.fetchone()[0]
        cur.execute(
            "INSERT INTO entities (person_id, kind, name, status) "
            "VALUES (%s, 'person', 'E', 'removed') RETURNING id::text",
            (pid,),
        )
        assert cur.fetchone()[0]
    finally:
        conn.close()
