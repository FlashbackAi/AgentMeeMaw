"""Migration 0035 — storybooks Python-render columns + view + grants.

Verifies the columns the storybook_render worker / Node contract rely on:
``collection`` (agent-written), ``pdf_url`` + ``page_urls`` (Node-written on
the storybook_render_complete NOTIFY), ``rendered_at`` + ``render_error``
(worker-written), and the appended ``active_storybooks`` read surface.
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")


@pytest.fixture
def db_conn(schema_applied: str):
    with psycopg.connect(schema_applied) as conn:
        yield conn


def test_storybooks_has_render_columns(db_conn) -> None:
    cols = {
        r[0]
        for r in db_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'storybooks'"
        ).fetchall()
    }
    assert {"collection", "pdf_url", "page_urls", "rendered_at", "render_error"} <= cols


def test_page_urls_defaults_to_empty_array(db_conn) -> None:
    row = db_conn.execute(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name = 'storybooks' AND column_name = 'page_urls'"
    ).fetchone()
    assert row is not None and "'[]'" in (row[0] or "")


def test_active_storybooks_view_exposes_render_fields(db_conn) -> None:
    cols = {
        r[0]
        for r in db_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'active_storybooks'"
        ).fetchall()
    }
    assert {"collection", "pdf_url", "page_urls", "rendered_at"} <= cols
