"""Reversible collaborator removal + restore (SP6a).

Status flips only — never DELETE, never touch edges/traits/questions/facts.
Caller owns the transaction (async cursor).
"""

from __future__ import annotations

from .schema import RemovalResult, RestoreResult

# Hide the contributor's own active moments; return their ids.
_HIDE_MOMENTS_SQL = """
    UPDATE moments SET status = 'removed'
     WHERE person_id = %(person_id)s
       AND told_by_user_id = %(user_id)s
       AND status = 'active'
    RETURNING id::text
"""

# Resurrect the nearest surviving-contributor ancestor of each removed moment.
# Recurse past a node only when that node is the removed user's, so the walk
# stops at the first surviving contributor (spec E1).
_RESURRECT_SQL = """
    WITH RECURSIVE chain AS (
        SELECT m.id, m.superseded_by, m.told_by_user_id, m.status
          FROM moments m
         WHERE m.superseded_by = ANY(%(removed_ids)s)
        UNION ALL
        SELECT m.id, m.superseded_by, m.told_by_user_id, m.status
          FROM moments m
          JOIN chain c ON m.superseded_by = c.id
         WHERE c.told_by_user_id IS NOT DISTINCT FROM %(user_id)s
    )
    UPDATE moments SET status = 'active'
     WHERE id IN (
         SELECT id FROM chain
          WHERE told_by_user_id IS DISTINCT FROM %(user_id)s
            AND status = 'superseded'
     )
    RETURNING id::text
"""

# Hide entities introduced by the user that no surviving active moment
# references (run AFTER moments are hidden + resurrected, spec E2).
_HIDE_ORPHAN_ENTITIES_SQL = """
    UPDATE entities e SET status = 'removed'
     WHERE e.person_id = %(person_id)s
       AND e.told_by_user_id = %(user_id)s
       AND e.status = 'active'
       AND NOT EXISTS (
           SELECT 1
             FROM active_edges ed
             JOIN active_moments m ON m.id = ed.from_id
            WHERE ed.from_kind = 'moment'
              AND ed.to_kind   = 'entity'
              AND ed.to_id     = e.id
              AND ed.edge_type IN ('involves', 'happened_at')
       )
    RETURNING id::text
"""

_REMOVE_ONBOARDING_SQL = """
    UPDATE collaborator_onboarding SET status = 'removed'
     WHERE person_id = %(person_id)s AND user_id = %(user_id)s AND status = 'active'
"""


async def remove_collaborator_async(
    cursor, *, person_id: str, user_id: str
) -> RemovalResult:
    params = {"person_id": person_id, "user_id": user_id}

    # 1. onboarding row -> removed
    await cursor.execute(_REMOVE_ONBOARDING_SQL, params)

    # 2. hide the user's active moments
    await cursor.execute(_HIDE_MOMENTS_SQL, params)
    removed_ids = [r[0] for r in await cursor.fetchall()]

    # 3. resurrect nearest surviving-contributor superseded ancestors
    resurrected = 0
    if removed_ids:
        await cursor.execute(
            _RESURRECT_SQL, {"removed_ids": removed_ids, "user_id": user_id}
        )
        resurrected = len(await cursor.fetchall())

    # 4. hide orphaned entities (after resurrection)
    await cursor.execute(_HIDE_ORPHAN_ENTITIES_SQL, params)
    entities_removed = len(await cursor.fetchall())

    return RemovalResult(
        person_id=person_id,
        user_id=user_id,
        moments_removed=len(removed_ids),
        entities_removed=entities_removed,
        moments_resurrected=resurrected,
    )


_RESTORE_MOMENTS_SQL = """
    UPDATE moments SET status = 'active'
     WHERE person_id = %(person_id)s
       AND told_by_user_id = %(user_id)s
       AND status = 'removed'
    RETURNING id::text
"""

_RESTORE_ENTITIES_SQL = """
    UPDATE entities SET status = 'active'
     WHERE person_id = %(person_id)s
       AND told_by_user_id = %(user_id)s
       AND status = 'removed'
    RETURNING id::text
"""

# Re-supersede predecessors that removal resurrected: an active moment whose
# superseded_by leads (through removed-user moments) to a just-restored moment.
# This MUST mirror the recursive resurrection walk in _RESURRECT_SQL: removal
# can resurrect a surviving-contributor ancestor buried 2+ hops behind the
# departing contributor's superseded moments, so restore must walk the same
# chain to re-supersede it. A flat (direct-predecessor-only) re-supersede would
# leave a buried ancestor active, breaking the round-trip (reviewer Issue 1).
_RE_SUPERSEDE_SQL = """
    WITH RECURSIVE chain AS (
        SELECT m.id, m.superseded_by, m.told_by_user_id, m.status
          FROM moments m
         WHERE m.superseded_by = ANY(%(restored_ids)s)
        UNION ALL
        SELECT m.id, m.superseded_by, m.told_by_user_id, m.status
          FROM moments m
          JOIN chain c ON m.superseded_by = c.id
         WHERE c.told_by_user_id IS NOT DISTINCT FROM %(user_id)s
    )
    UPDATE moments SET status = 'superseded'
     WHERE id IN (
         SELECT id FROM chain
          WHERE told_by_user_id IS DISTINCT FROM %(user_id)s
            AND status = 'active'
     )
    RETURNING id::text
"""

# Restore the onboarding row only if no active row already exists for this
# (person, user) — tolerates a re-session-under-same-id inconsistency (E10).
_RESTORE_ONBOARDING_SQL = """
    UPDATE collaborator_onboarding SET status = 'active'
     WHERE person_id = %(person_id)s AND user_id = %(user_id)s AND status = 'removed'
       AND NOT EXISTS (
           SELECT 1 FROM collaborator_onboarding
            WHERE person_id = %(person_id)s AND user_id = %(user_id)s AND status = 'active'
       )
"""


async def restore_collaborator_async(
    cursor, *, person_id: str, user_id: str
) -> RestoreResult:
    params = {"person_id": person_id, "user_id": user_id}

    await cursor.execute(_RESTORE_MOMENTS_SQL, params)
    restored_ids = [r[0] for r in await cursor.fetchall()]

    await cursor.execute(_RESTORE_ENTITIES_SQL, params)
    entities_restored = len(await cursor.fetchall())

    re_superseded = 0
    if restored_ids:
        await cursor.execute(
            _RE_SUPERSEDE_SQL, {"restored_ids": restored_ids, "user_id": user_id}
        )
        re_superseded = len(await cursor.fetchall())

    await cursor.execute(_RESTORE_ONBOARDING_SQL, params)

    return RestoreResult(
        person_id=person_id,
        user_id=user_id,
        moments_restored=len(restored_ids),
        entities_restored=entities_restored,
        moments_re_superseded=re_superseded,
    )
