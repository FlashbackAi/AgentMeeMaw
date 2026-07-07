import os

import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def test_usage_events_table_and_views_exist(schema_applied):
    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'usage_events' ORDER BY column_name"
        )
        cols = {r[0] for r in cur.fetchall()}
        assert {
            "id", "service", "feature", "provider", "model",
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "units", "unit_type", "cost_usd",
            "person_id", "session_id", "created_at",
        } <= cols

        for view in (
            "dashboard_cost_by_feature",
            "dashboard_cost_by_model",
            "dashboard_storybooks",
            "dashboard_tributes",
            "dashboard_content_counts",
            "dashboard_worker_health",
        ):
            cur.execute("SELECT to_regclass(%s)", (view,))
            assert cur.fetchone()[0] is not None, f"missing view {view}"
