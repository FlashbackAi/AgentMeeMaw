"""Backfill the standalone (campaign_id IS NULL) tribute row for legacies
created before the two-meter model (design 2026-07-22).

For every person that has a tribute theme but no non-superseded standalone
tribute row, create one (draft, campaign_id NULL) linked to that theme — the
same get-or-create ``insert_person`` now runs at creation. Idempotent: skips
persons that already have a standalone row, so it's safe to re-run.

Usage:
    set -a; source /etc/flashback-agent.env; set +a
    python scripts/backfill_standalone_tributes.py [--dry-run]
"""
from __future__ import annotations

import asyncio
import os
import sys

import psycopg

from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)


async def _run(dry_run: bool) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2

    # Persons with NO active/non-superseded standalone tribute row.
    find_sql = """
        SELECT p.id::text
          FROM persons p
         WHERE NOT EXISTS (
             SELECT 1 FROM tributes t
              WHERE t.person_id = p.id
                AND t.campaign_id IS NULL
                AND t.status <> 'superseded'
         )
         ORDER BY p.created_at
    """
    # Reuse the tribute theme if present, else seed it, then create the row.
    ensure_theme_sql = """
        INSERT INTO themes (person_id, kind, slug, display_name, description, state)
        VALUES (%(pid)s, 'tribute', %(slug)s, %(name)s, %(desc)s, 'locked')
        ON CONFLICT DO NOTHING
    """
    theme_id_sql = """
        SELECT id::text FROM themes
         WHERE person_id = %(pid)s AND slug = %(slug)s AND status = 'active'
         LIMIT 1
    """
    insert_row_sql = """
        INSERT INTO tributes (person_id, theme_id, campaign_id, status)
        VALUES (%(pid)s, %(tid)s, NULL, 'draft')
    """

    async with await psycopg.AsyncConnection.connect(url) as conn:
        async with conn.cursor() as cur:
            await cur.execute(find_sql)
            pids = [r[0] for r in await cur.fetchall()]
            print(f"{len(pids)} legacies need a standalone tribute row"
                  f"{' (dry-run)' if dry_run else ''}")
            if dry_run:
                return 0
            created = 0
            for pid in pids:
                await cur.execute(ensure_theme_sql, {
                    "pid": pid, "slug": TRIBUTE_SLUG,
                    "name": TRIBUTE_DISPLAY_NAME, "desc": TRIBUTE_DESCRIPTION,
                })
                await cur.execute(theme_id_sql, {"pid": pid, "slug": TRIBUTE_SLUG})
                trow = await cur.fetchone()
                if trow is None:
                    print(f"  ! no tribute theme resolvable for {pid}, skipping")
                    continue
                await cur.execute(insert_row_sql, {"pid": pid, "tid": trow[0]})
                created += 1
            await conn.commit()
            print(f"created {created} standalone tribute rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run("--dry-run" in sys.argv)))
