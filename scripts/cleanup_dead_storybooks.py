"""Clear storybooks stranded at status='generating' by the retired
pre-collection render pipeline (prod 2026-07-29).

psycopg twin of ``scripts/cleanup_dead_storybooks.sql`` for boxes without psql.
Same narrow predicate, same inspect-then-commit shape.

Targets: status='generating' AND collection IS NULL AND pdf_url IS NULL.
A live book always carries a collection, so an in-flight render cannot match.
Aborts unless the target count is exactly the 5 rows verified in prod, so a
drifted database is never silently mass-updated.

Usage:
    set -a; source /etc/flashback-agent.env; set +a   # or export DATABASE_URL
    python scripts/cleanup_dead_storybooks.py --dry-run
    python scripts/cleanup_dead_storybooks.py
"""

from __future__ import annotations

import os
import sys

import psycopg

EXPECTED_TARGETS = 5

SELECT_TARGETS = """
    SELECT s.id::text, p.name, s.created_at::date, s.status,
           (now() - s.created_at)::interval(0) AS stuck_for
      FROM storybooks s
      JOIN persons p ON p.id = s.person_id
     WHERE s.status = 'generating'
       AND s.collection IS NULL
       AND s.pdf_url IS NULL
     ORDER BY s.created_at
"""

UPDATE_TARGETS = """
    UPDATE storybooks
       SET status = 'failed'
     WHERE status = 'generating'
       AND collection IS NULL
       AND pdf_url IS NULL
"""


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2

    with psycopg.connect(url, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(SELECT_TARGETS)
            rows = cur.fetchall()
            print(f"targets ({len(rows)}):")
            for r in rows:
                print("   " + " | ".join(str(v) for v in r))

            if len(rows) != EXPECTED_TARGETS:
                print(
                    f"\nABORT: expected exactly {EXPECTED_TARGETS} targets, "
                    f"found {len(rows)}. Re-verify before running.",
                    file=sys.stderr,
                )
                return 1

            if dry_run:
                print("\n(dry-run: nothing written)")
                return 0

            cur.execute(UPDATE_TARGETS)
            print(f"\nmarked failed: {cur.rowcount}")
            conn.commit()

            cur.execute(
                """
                SELECT count(*) FROM storybooks
                 WHERE status = 'generating'
                   AND created_at < now() - interval '1 day'
                """
            )
            print("stale 'generating' remaining (want 0):", cur.fetchone()[0])
            cur.execute(
                "SELECT status, count(*) FROM storybooks GROUP BY 1 ORDER BY 1"
            )
            print("\nstorybooks by status:")
            for status_val, count in cur.fetchall():
                print(f"   {status_val}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
