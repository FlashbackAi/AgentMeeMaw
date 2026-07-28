"""insert_person seeds a tribute theme discoverable via the unlock path."""

from __future__ import annotations

import re
from pathlib import Path

from flashback.persons.repository import insert_person

REPO_ROOT = Path(__file__).resolve().parents[2]
M0028_UP = REPO_ROOT / "migrations" / "0028_backfill_tribute_theme.up.sql"


def test_0028_backfills_tribute_theme() -> None:
    sql = M0028_UP.read_text(encoding="utf-8")
    assert re.search(r"INSERT\s+INTO\s+themes", sql, re.I)
    assert "'tribute'" in sql
    assert re.search(r"ON\s+CONFLICT", sql, re.I)


async def test_insert_person_seeds_discoverable_tribute_theme(async_pool) -> None:
    created = await insert_person(async_pool, name="Dad", relationship="father")
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            # The Node read surface (active_themes_with_tier) must expose it.
            await cur.execute(
                """
                SELECT kind, state
                  FROM active_themes_with_tier
                 WHERE person_id = %s AND kind = 'tribute'
                """,
                (str(created.person_id),),
            )
            row = await cur.fetchone()
    assert row is not None, "tribute theme not discoverable via the view"
    assert row[0] == "tribute"
    assert row[1] == "locked"
