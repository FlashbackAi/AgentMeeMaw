"""Persistence helper for ``latest_generation_context``.

Single point of truth for writing the JSONB context column on the four
artifact-bearing tables (``persons``, ``moments``, ``entities``,
``threads``). Used by every code path that composes an artifact prompt
— HTTP edit / regenerate routes, the persons-onboarding route, the
extraction worker's post-commit hook, the thread detector, and the
node_edits refinement engine.

The agent writes this column BEFORE pushing the (trigger-only) SQS
message. The Node worker reads it from Postgres at job processing time.
See migration 0023 and CLAUDE.md §3 (artifact-generation rule).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

_ALLOWED_TABLES = frozenset({"persons", "moments", "entities", "threads"})


async def write_latest_generation_context_async(
    cur,
    *,
    table: str,
    record_id: str | UUID,
    context: dict[str, Any],
) -> None:
    """Async-cursor version. Used by the HTTP routes."""
    _guard(table)
    query = (
        f"UPDATE {table} "
        f"SET latest_generation_context = %s::jsonb "
        f"WHERE id = %s"
    )
    await cur.execute(query, (json.dumps(context), str(record_id)))


def write_latest_generation_context_sync(
    cur,
    *,
    table: str,
    record_id: str | UUID,
    context: dict[str, Any],
) -> None:
    """Sync-cursor version. Used by the extraction worker, thread detector."""
    _guard(table)
    query = (
        f"UPDATE {table} "
        f"SET latest_generation_context = %s::jsonb "
        f"WHERE id = %s"
    )
    cur.execute(query, (json.dumps(context), str(record_id)))


def _guard(table: str) -> None:
    # Tables are inlined into the SQL string (psycopg can't parameterize
    # identifiers); validate against an allow-list to keep this safe.
    if table not in _ALLOWED_TABLES:
        raise ValueError(
            f"latest_generation_context not supported for table {table!r}; "
            f"allowed: {sorted(_ALLOWED_TABLES)}"
        )
