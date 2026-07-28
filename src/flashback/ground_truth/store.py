"""Reads and precedence-aware writes for ``persons.ground_truth``.

Precedence: ``user_edit > tap > onboarding > inferred``. A write at a
lower rank than the stored value is dropped; equal rank refines (so a
better inference replaces an earlier one). Inferred writes additionally
require ``confidence == "high"`` (invariant #6 — under-extract).

``apply_field`` is pure (returns a new dict or ``None`` on rejection) so
the rules are testable without a database. The async helpers serve the
HTTP/orchestrator side; the ``*_sync`` helpers serve the extraction
worker, which runs sync cursors inside its own transaction.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog

from flashback.ground_truth.registry import REGISTRY_BY_KEY

log = structlog.get_logger("flashback.ground_truth")

PROVENANCE_RANK: dict[str, int] = {
    "inferred": 0,
    "onboarding": 1,
    "tap": 2,
    "user_edit": 3,
}

_SELECT_FOR_UPDATE = (
    "SELECT ground_truth FROM persons WHERE id = %s FOR UPDATE"
)
_SELECT = "SELECT ground_truth FROM persons WHERE id = %s"
_UPDATE = "UPDATE persons SET ground_truth = %s::jsonb WHERE id = %s"


def apply_field(
    ground_truth: dict[str, Any] | None,
    *,
    field: str,
    value: Any,
    provenance: str,
    confidence: str,
    now: datetime,
) -> dict[str, Any] | None:
    """Return a NEW ground_truth dict with the write applied, or None if
    the write is rejected (unknown field, empty value, low-confidence
    inference, or insufficient provenance)."""
    spec = REGISTRY_BY_KEY.get(field)
    if spec is None or provenance not in PROVENANCE_RANK:
        return None
    if provenance == "inferred" and confidence != "high":
        return None

    cleaned = _clean_value(value, spec.value_type)
    if cleaned is None:
        return None

    current = dict(ground_truth or {})
    existing = current.get(field)
    if isinstance(existing, dict):
        existing_rank = PROVENANCE_RANK.get(str(existing.get("provenance")), 0)
        if PROVENANCE_RANK[provenance] < existing_rank:
            return None
        if spec.value_type == "list":
            merged = [v for v in (existing.get("value") or []) if isinstance(v, str)]
            for item in cleaned:
                if item not in merged:
                    merged.append(item)
            cleaned = merged

    current[field] = {
        "value": cleaned,
        "provenance": provenance,
        "confidence": confidence,
        "updated_at": now.isoformat(),
    }
    return current


def _clean_value(value: Any, value_type: str) -> Any | None:
    if value_type == "list":
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list):
            return None
        cleaned = [v.strip() for v in items if isinstance(v, str) and v.strip()]
        return cleaned or None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


# --- async (HTTP / orchestrator side) --------------------------------------


async def fetch_ground_truth(db_pool, person_id: UUID | str) -> dict[str, Any]:
    """Best-effort read for prompt enrichment. Never raises — every
    consumer treats missing ground truth as 'nothing known yet'."""
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_SELECT, (str(person_id),))
                row = await cur.fetchone()
    except Exception as exc:  # noqa: BLE001 - enrichment must not block a turn
        log.warning(
            "ground_truth.fetch_failed",
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return {}
    if row is None or not isinstance(row[0], dict):
        return {}
    return row[0]


async def upsert_ground_truth_field(
    cur,
    person_id: UUID | str,
    *,
    field: str,
    value: Any,
    provenance: str,
    confidence: str = "high",
) -> bool:
    """Apply one write inside the caller's transaction. Returns True if
    written. ``cur`` is an async psycopg cursor."""
    await cur.execute(_SELECT_FOR_UPDATE, (str(person_id),))
    row = await cur.fetchone()
    current = row[0] if row is not None and isinstance(row[0], dict) else {}
    updated = apply_field(
        current, field=field, value=value, provenance=provenance,
        confidence=confidence, now=datetime.now(timezone.utc),
    )
    if updated is None:
        log.info("ground_truth.write_rejected", field=field, provenance=provenance)
        return False
    await cur.execute(_UPDATE, (json.dumps(updated), str(person_id)))
    log.info("ground_truth.written", field=field, provenance=provenance)
    return True


# --- sync (extraction worker side) ------------------------------------------


def fetch_ground_truth_sync(cursor, person_id: str) -> dict[str, Any]:
    cursor.execute(_SELECT, (person_id,))
    row = cursor.fetchone()
    if row is None or not isinstance(row[0], dict):
        return {}
    return row[0]


def apply_observations_sync(
    cursor, person_id: str, observations: list
) -> int:
    """Persist extraction-emitted observations (provenance='inferred').
    Only high-confidence observations are written (apply_field enforces).
    Returns the number written. Runs inside the worker's transaction."""
    if not observations:
        return 0
    cursor.execute(_SELECT_FOR_UPDATE, (person_id,))
    row = cursor.fetchone()
    current = row[0] if row is not None and isinstance(row[0], dict) else {}
    written = 0
    now = datetime.now(timezone.utc)
    for obs in observations:
        updated = apply_field(
            current, field=obs.field, value=obs.value,
            provenance="inferred", confidence=obs.confidence, now=now,
        )
        if updated is not None:
            current = updated
            written += 1
    if written:
        cursor.execute(_UPDATE, (json.dumps(current), person_id))
    return written


def recompute_era_span_sync(cursor, person_id: str) -> None:
    """Derive era_span (sorted decade list) from active moments' time
    anchors. Code-derived — never asked, never LLM-emitted."""
    cursor.execute(
        """
        SELECT DISTINCT COALESCE(
                   time_anchor->>'decade',
                   ((((time_anchor->>'year')::int) / 10) * 10)::text || 's'
               )
          FROM moments
         WHERE person_id = %s
           AND status = 'active'
           AND (time_anchor->>'decade' IS NOT NULL
                OR time_anchor->>'year' IS NOT NULL)
        """,
        (person_id,),
    )
    decades = sorted({row[0] for row in cursor.fetchall() if row[0]})
    if not decades:
        return
    cursor.execute(_SELECT_FOR_UPDATE, (person_id,))
    row = cursor.fetchone()
    current = row[0] if row is not None and isinstance(row[0], dict) else {}
    current["era_span"] = {
        "value": decades,
        "provenance": "inferred",
        "confidence": "high",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    cursor.execute(_UPDATE, (json.dumps(current), person_id))
