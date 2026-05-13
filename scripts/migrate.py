"""Apply SQL migrations in production and local environments.

Usage:
    python scripts/migrate.py
    python scripts/migrate.py --dry-run

The runner stores applied filenames and sha256 checksums in
``schema_migrations``. A checksum change for an already-applied
migration is treated as a deploy-blocking error.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "migrations"
MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename text PRIMARY KEY,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise SystemExit("DATABASE_URL is required")
    return value


def _up_migrations() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.up.sql"))


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strip_transaction_markers(sql: str) -> str:
    """Remove bare BEGIN/COMMIT lines from migration SQL.

    Migration files are written for use with psql (which needs explicit
    transaction markers). Inside psycopg's conn.transaction() the script
    manages its own savepoints, so bare BEGIN/COMMIT would commit the
    outer transaction mid-flight and corrupt savepoint accounting.

    Lines inside a $$ ... $$ dollar-quoted block (e.g. plpgsql function
    bodies) must be left alone, otherwise the BEGIN keyword that opens
    the function body would be stripped.
    """
    out = []
    in_dollar = False
    for line in sql.splitlines():
        if not in_dollar:
            upper = line.strip().rstrip(";").upper()
            if upper in {"BEGIN", "COMMIT", "ROLLBACK"}:
                continue
        if line.count("$$") % 2 == 1:
            in_dollar = not in_dollar
        out.append(line)
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Flashback SQL migrations")
    parser.add_argument("--dry-run", action="store_true", help="print pending migrations")
    args = parser.parse_args()

    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(MIGRATION_TABLE_SQL)
            cur.execute("SELECT filename, checksum FROM schema_migrations")
            applied = dict(cur.fetchall())

        pending: list[tuple[Path, str]] = []
        for path in _up_migrations():
            checksum = _checksum(path)
            previous = applied.get(path.name)
            if previous is None:
                pending.append((path, checksum))
                continue
            if previous != checksum:
                raise SystemExit(
                    f"checksum mismatch for applied migration {path.name}; "
                    "create a new migration instead of editing history"
                )

        if args.dry_run:
            for path, _ in pending:
                print(path.name)
            return 0

        for path, checksum in pending:
            sql = _strip_transaction_markers(path.read_text(encoding="utf-8-sig"))
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        """
                        INSERT INTO schema_migrations (filename, checksum)
                        VALUES (%s, %s)
                        """,
                        (path.name, checksum),
                    )
            print(f"applied {path.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
