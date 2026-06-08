"""Migration 0025 shape (DB-touching). The db_pool fixture applies all
migrations, so we assert the resulting schema directly."""

from __future__ import annotations


def test_processed_extractions_has_signal_columns(db_pool):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                  FROM information_schema.columns
                 WHERE table_name = 'processed_extractions'
                   AND column_name IN
                       ('entities_written','traits_written','is_final','status')
                 ORDER BY column_name
                """
            )
            rows = dict(cur.fetchall())
    assert rows == {
        "entities_written": "integer",
        "is_final": "boolean",
        "status": "text",
        "traits_written": "integer",
    }


def test_session_extraction_status_view_exists(db_pool):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_name = 'session_extraction_status'
                 ORDER BY column_name
                """
            )
            cols = {r[0] for r in cur.fetchall()}
    assert {
        "session_id", "person_id", "segment_message_id",
        "moments_written", "entities_written", "traits_written",
        "is_final", "status", "processed_at",
    } <= cols
