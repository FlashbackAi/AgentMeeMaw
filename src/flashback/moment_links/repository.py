"""Persistence for SP5 same-event links + contradiction review items.

Sync helpers run inside the Extraction Worker's transaction (psycopg sync
cursor). Async helpers serve the read/action HTTP endpoints. Record rows
store moment ids only; told_by_* is resolved live via JOIN to moments at
read time (spec D5).
"""

from __future__ import annotations

import structlog

log = structlog.get_logger("flashback.moment_links")


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    """Stable A/B order (smaller UUID string first) so mirror pairs collapse
    under the partial unique index."""
    return (a, b) if str(a) <= str(b) else (b, a)


def insert_same_event_link(
    cursor, *, person_id: str, moment_a_id: str, moment_b_id: str, reason: str | None
) -> str | None:
    a, b = canonical_pair(moment_a_id, moment_b_id)
    cursor.execute(
        """
        INSERT INTO moment_same_event_links
              (person_id, moment_a_id, moment_b_id, reason)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (moment_a_id, moment_b_id) WHERE status = 'active'
        DO NOTHING
        RETURNING id::text
        """,
        (person_id, a, b, reason),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def insert_contradiction(
    cursor, *, person_id: str, moment_a_id: str, moment_b_id: str, reason: str | None
) -> str | None:
    a, b = canonical_pair(moment_a_id, moment_b_id)
    cursor.execute(
        """
        INSERT INTO moment_contradictions
              (person_id, moment_a_id, moment_b_id, reason)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (moment_a_id, moment_b_id) WHERE status = 'pending'
        DO NOTHING
        RETURNING id::text
        """,
        (person_id, a, b, reason),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def repoint_records_on_supersession(cursor, *, old_id: str, new_id: str) -> None:
    """Keep SP5 records pointing at the active moment after a supersession.

    Extends invariant #5 to same-event links + contradiction review items.
    Active links / pending contradictions referencing ``old_id`` are either
    collapsed (if repointing would create a self-pair) or have ``old_id``
    swapped for ``new_id`` with the A/B order re-canonicalized. Terminal rows
    (unlinked / dismissed) are left untouched.
    """
    _repoint_table(
        cursor,
        table="moment_same_event_links",
        live_status="active",
        terminal_status="unlinked",
        terminal_extra="",
        old_id=old_id,
        new_id=new_id,
    )
    _repoint_table(
        cursor,
        table="moment_contradictions",
        live_status="pending",
        terminal_status="dismissed",
        terminal_extra=", resolved_at = now()",
        old_id=old_id,
        new_id=new_id,
    )


def _repoint_table(
    cursor, *, table, live_status, terminal_status, terminal_extra, old_id, new_id
):
    cursor.execute(
        f"""
        SELECT id::text, moment_a_id::text, moment_b_id::text
          FROM {table}
         WHERE status = %s
           AND (moment_a_id = %s OR moment_b_id = %s)
        """,
        (live_status, old_id, old_id),
    )
    for row_id, a, b in cursor.fetchall():
        partner = b if a == old_id else a
        if partner == new_id:
            cursor.execute(
                f"UPDATE {table} SET status = %s{terminal_extra} WHERE id = %s",
                (terminal_status, row_id),
            )
            continue
        na, nb = canonical_pair(new_id, partner)
        cursor.execute(
            f"UPDATE {table} SET moment_a_id = %s, moment_b_id = %s WHERE id = %s",
            (na, nb, row_id),
        )


# ---------------------------------------------------------------------------
# Async read + action helpers (HTTP endpoints)
# ---------------------------------------------------------------------------

_EVENT_LINKS_SQL = """
SELECT l.id, l.person_id, l.moment_a_id, l.moment_b_id, l.reason, l.status,
       l.acknowledged_at, l.created_at,
       ma.title AS moment_a_title, mb.title AS moment_b_title,
       ma.told_by_user_id AS told_by_a_user_id, coa.display_name AS told_by_a_display_name,
       mb.told_by_user_id AS told_by_b_user_id, cob.display_name AS told_by_b_display_name
  FROM moment_same_event_links l
  JOIN active_moments ma ON ma.id = l.moment_a_id
  JOIN active_moments mb ON mb.id = l.moment_b_id
  LEFT JOIN collaborator_onboarding coa
        ON coa.person_id = l.person_id AND coa.user_id = ma.told_by_user_id AND coa.status = 'active'
  LEFT JOIN collaborator_onboarding cob
        ON cob.person_id = l.person_id AND cob.user_id = mb.told_by_user_id AND cob.status = 'active'
 WHERE l.person_id = %(person_id)s
   AND l.status = 'active'
   AND (%(include_ack)s OR l.acknowledged_at IS NULL)
 ORDER BY l.created_at DESC
"""

_CONTRADICTIONS_SQL = """
SELECT c.id, c.person_id, c.moment_a_id, c.moment_b_id, c.reason, c.status,
       c.created_at, c.resolved_at,
       ma.title AS moment_a_title, mb.title AS moment_b_title,
       ma.told_by_user_id AS told_by_a_user_id, coa.display_name AS told_by_a_display_name,
       mb.told_by_user_id AS told_by_b_user_id, cob.display_name AS told_by_b_display_name
  FROM moment_contradictions c
  JOIN active_moments ma ON ma.id = c.moment_a_id
  JOIN active_moments mb ON mb.id = c.moment_b_id
  LEFT JOIN collaborator_onboarding coa
        ON coa.person_id = c.person_id AND coa.user_id = ma.told_by_user_id AND coa.status = 'active'
  LEFT JOIN collaborator_onboarding cob
        ON cob.person_id = c.person_id AND cob.user_id = mb.told_by_user_id AND cob.status = 'active'
 WHERE c.person_id = %(person_id)s
   AND c.status = 'pending'
 ORDER BY c.created_at DESC
"""


async def list_event_links_async(cursor, *, person_id, include_acknowledged=False):
    from .schema import SameEventLink

    await cursor.execute(
        _EVENT_LINKS_SQL,
        {"person_id": person_id, "include_ack": include_acknowledged},
    )
    rows = await cursor.fetchall()
    return [_row_to_link(SameEventLink, r) for r in rows]


async def list_contradictions_async(cursor, *, person_id):
    from .schema import ContradictionItem

    await cursor.execute(_CONTRADICTIONS_SQL, {"person_id": person_id})
    rows = await cursor.fetchall()
    return [_row_to_contradiction(ContradictionItem, r) for r in rows]


def _row_to_link(model, r):
    return model(
        id=r[0], person_id=r[1], moment_a_id=r[2], moment_b_id=r[3], reason=r[4],
        status=r[5], acknowledged_at=r[6], created_at=r[7],
        moment_a_title=r[8] or "", moment_b_title=r[9] or "",
        told_by_a_user_id=r[10], told_by_a_display_name=r[11],
        told_by_b_user_id=r[12], told_by_b_display_name=r[13],
    )


def _row_to_contradiction(model, r):
    return model(
        id=r[0], person_id=r[1], moment_a_id=r[2], moment_b_id=r[3], reason=r[4],
        status=r[5], created_at=r[6], resolved_at=r[7],
        moment_a_title=r[8] or "", moment_b_title=r[9] or "",
        told_by_a_user_id=r[10], told_by_a_display_name=r[11],
        told_by_b_user_id=r[12], told_by_b_display_name=r[13],
    )


async def acknowledge_event_link_async(cursor, *, link_id):
    await cursor.execute(
        "UPDATE moment_same_event_links SET acknowledged_at = now(), updated_at = now() "
        "WHERE id = %s AND status = 'active'",
        (link_id,),
    )
    return cursor.rowcount > 0


async def unlink_event_link_async(cursor, *, link_id):
    await cursor.execute(
        "UPDATE moment_same_event_links SET status = 'unlinked', updated_at = now() "
        "WHERE id = %s AND status = 'active'",
        (link_id,),
    )
    return cursor.rowcount > 0


async def dismiss_contradiction_async(cursor, *, item_id):
    await cursor.execute(
        "UPDATE moment_contradictions SET status = 'dismissed', resolved_at = now() "
        "WHERE id = %s AND status = 'pending'",
        (item_id,),
    )
    return cursor.rowcount > 0
