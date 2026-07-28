"""Text regression checks for the 0027 tributes migration."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UP = REPO_ROOT / "migrations" / "0027_tributes.up.sql"
DOWN = REPO_ROOT / "migrations" / "0027_tributes.down.sql"


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_0027_creates_tributes_table() -> None:
    sql = _sql(UP)
    assert re.search(r"CREATE\s+TABLE\s+tributes\b", sql, re.I)
    assert "message_text" in sql
    assert "message_source_turns" in sql
    assert "latest_generation_context" in sql


def test_0027_allows_tribute_theme_kind() -> None:
    sql = _sql(UP)
    assert re.search(
        r"kind\s+IN\s*\(\s*'universal'\s*,\s*'emergent'\s*,\s*'tribute'\s*\)",
        sql,
        re.I,
    )


def test_0027_creates_status_view_with_percent_and_ready() -> None:
    sql = _sql(UP)
    assert re.search(r"CREATE\s+VIEW\s+tribute_status\s+AS", sql, re.I)
    assert "percent" in sql
    assert "ready" in sql
    assert "appearance_present" in sql
    assert "signature_present" in sql


def test_0027_down_drops_view_and_table_and_restores_kind() -> None:
    sql = _sql(DOWN)
    assert re.search(r"DROP\s+VIEW\s+IF\s+EXISTS\s+tribute_status", sql, re.I)
    assert re.search(r"DROP\s+TABLE\s+IF\s+EXISTS\s+tributes", sql, re.I)
    assert re.search(
        r"kind\s+IN\s*\(\s*'universal'\s*,\s*'emergent'\s*\)", sql, re.I
    )
