"""Regression checks for the 0022 theme UX migration."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_0022_UP = REPO_ROOT / "migrations" / "0022_themes_eligibility_and_drafts.up.sql"
MIGRATION_0022_DOWN = REPO_ROOT / "migrations" / "0022_themes_eligibility_and_drafts.down.sql"


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_0022_rebuilds_active_themes_with_draft_column() -> None:
    sql = _sql(MIGRATION_0022_UP)

    assert re.search(r"DROP\s+VIEW\s+IF\s+EXISTS\s+active_themes\b", sql, re.I)
    assert re.search(r"CREATE\s+VIEW\s+active_themes\s+AS", sql, re.I)

    active_themes_body = re.search(
        r"CREATE\s+VIEW\s+active_themes\s+AS(?P<body>.*?)DROP\s+VIEW\s+IF\s+EXISTS\s+active_themes_with_tier",
        sql,
        re.I | re.S,
    )
    assert active_themes_body, "could not locate rebuilt active_themes view"
    assert "archetype_answers_draft" in active_themes_body.group("body")


def test_0022_theme_tier_view_exposes_frontend_lock_state() -> None:
    sql = _sql(MIGRATION_0022_UP)

    assert "eligibility" in sql
    assert "archetype_progress" in sql
    assert "jsonb_array_elements(t.archetype_answers_draft)" in sql


def test_0022_down_restores_active_themes_without_draft_column() -> None:
    sql = _sql(MIGRATION_0022_DOWN)

    assert re.search(r"DROP\s+VIEW\s+IF\s+EXISTS\s+active_themes\b", sql, re.I)
    assert re.search(r"CREATE\s+VIEW\s+active_themes\s+AS", sql, re.I)
    assert "ALTER TABLE themes DROP COLUMN IF EXISTS archetype_answers_draft" in sql
