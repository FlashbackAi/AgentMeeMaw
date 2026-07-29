"""Persistence helpers for user-approved entity merges."""

from __future__ import annotations

from typing import Callable

import structlog
from psycopg.types.json import Json

from .schema import (
    AutoMergeNotification,
    IdentityMergeActionResponse,
    IdentityMergeSuggestion,
    UnmergeResponse,
)

log = structlog.get_logger("flashback.identity_merges")


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


async def list_suggestions_async(
    cursor,
    *,
    person_id: str,
    status: str = "pending",
) -> list[IdentityMergeSuggestion]:
    await cursor.execute(
        """
        SELECT s.id, s.person_id,
               s.source_entity_id, src.name AS source_entity_name,
               src.description AS source_entity_description,
               s.target_entity_id, tgt.name AS target_entity_name,
               tgt.description AS target_entity_description,
               s.proposed_alias, s.reason, s.source, s.status, s.created_at
          FROM identity_merge_suggestions s
          JOIN entities src ON src.id = s.source_entity_id
          JOIN entities tgt ON tgt.id = s.target_entity_id
         WHERE s.person_id = %s
           AND s.status = %s
           AND src.status = 'active'
           AND tgt.status = 'active'
         ORDER BY s.created_at DESC
        """,
        (person_id, status),
    )
    rows = await cursor.fetchall()
    return [
        IdentityMergeSuggestion(
            id=row[0],
            person_id=row[1],
            source_entity_id=row[2],
            source_entity_name=row[3],
            source_entity_description=row[4],
            target_entity_id=row[5],
            target_entity_name=row[6],
            target_entity_description=row[7],
            proposed_alias=row[8],
            reason=row[9],
            source=row[10],
            status=row[11],
            created_at=row[12],
        )
        for row in rows
    ]


async def approve_merge_async(
    cursor,
    *,
    suggestion_id: str,
    push_embedding: Callable[..., str] | None,
    embedding_model: str,
    embedding_model_version: str,
) -> IdentityMergeActionResponse | None:
    row = await _lock_pending_suggestion(cursor, suggestion_id=suggestion_id)
    if row is None:
        return None

    person_id, source_id, target_id, proposed_alias = row

    snapshot = await _merge_entity_rows(
        cursor,
        person_id=person_id,
        source_id=source_id,
        target_id=target_id,
        proposed_alias=proposed_alias,
    )
    await cursor.execute(
        """
        UPDATE identity_merge_suggestions
           SET status = 'approved',
               approved_at = now(),
               undo_snapshot = %s
         WHERE id = %s
        """,
        (Json(snapshot), suggestion_id),
    )
    await _reject_sibling_suggestions(
        cursor,
        person_id=person_id,
        source_id=source_id,
        approved_suggestion_id=suggestion_id,
    )
    source_text = await _target_source_text(cursor, target_id=target_id)
    if push_embedding is not None and source_text:
        push_embedding(
            record_type="entity",
            record_id=target_id,
            source_text=source_text,
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )

    return IdentityMergeActionResponse(
        suggestion_id=suggestion_id,
        person_id=person_id,
        source_entity_id=source_id,
        target_entity_id=target_id,
        status="approved",
    )


async def reject_merge_async(
    cursor,
    *,
    suggestion_id: str,
) -> IdentityMergeActionResponse | None:
    row = await _lock_pending_suggestion(cursor, suggestion_id=suggestion_id)
    if row is None:
        return None
    person_id, source_id, target_id, _alias = row
    await cursor.execute(
        """
        UPDATE identity_merge_suggestions
           SET status = 'rejected',
               rejected_at = now()
         WHERE id = %s
        """,
        (suggestion_id,),
    )
    return IdentityMergeActionResponse(
        suggestion_id=suggestion_id,
        person_id=person_id,
        source_entity_id=source_id,
        target_entity_id=target_id,
        status="rejected",
    )


async def auto_merge_async(
    cursor,
    *,
    person_id: str,
    source_id: str,
    target_id: str,
    proposed_alias: str | None,
    confidence: str,
    notification_text: str,
    push_embedding: Callable[..., str] | None,
    embedding_model: str,
    embedding_model_version: str,
) -> str | None:
    """Apply a high-confidence merge silently and record it for the user.

    Inserts an ``auto_merged`` suggestion row carrying the undo snapshot
    and the user-facing notification text, so the merge is both auditable
    and reversible via :func:`unmerge_async`. Returns the new row id, or
    ``None`` if either entity is no longer active.
    """
    try:
        snapshot = await _merge_entity_rows(
            cursor,
            person_id=person_id,
            source_id=source_id,
            target_id=target_id,
            proposed_alias=proposed_alias,
        )
    except ValueError:
        return None

    await cursor.execute(
        """
        INSERT INTO identity_merge_suggestions
              (person_id, source_entity_id, target_entity_id,
               proposed_alias, reason, source, status,
               confidence, notification_text, undo_snapshot, auto_merged_at)
        VALUES (%s, %s, %s, %s, %s, 'scanner', 'auto_merged',
                %s, %s, %s, now())
        RETURNING id::text
        """,
        (
            person_id,
            source_id,
            target_id,
            proposed_alias,
            notification_text,
            confidence,
            notification_text,
            Json(snapshot),
        ),
    )
    row = await cursor.fetchone()
    new_id = row[0] if row else None

    source_text = await _target_source_text(cursor, target_id=target_id)
    if push_embedding is not None and source_text:
        push_embedding(
            record_type="entity",
            record_id=target_id,
            source_text=source_text,
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )
    return new_id


async def list_auto_merged_async(
    cursor,
    *,
    person_id: str,
    only_unacknowledged: bool = True,
) -> list[AutoMergeNotification]:
    """Notification feed Node polls to render auto-merge toasts."""
    ack_filter = "AND s.acknowledged = false" if only_unacknowledged else ""
    await cursor.execute(
        f"""
        SELECT s.id, s.person_id,
               s.source_entity_id, s.target_entity_id, tgt.name,
               s.notification_text, s.confidence, s.acknowledged,
               s.auto_merged_at
          FROM identity_merge_suggestions s
          JOIN entities tgt ON tgt.id = s.target_entity_id
         WHERE s.person_id = %s
           AND s.status = 'auto_merged'
           {ack_filter}
         ORDER BY s.auto_merged_at DESC
        """,
        (person_id,),
    )
    rows = await cursor.fetchall()
    return [
        AutoMergeNotification(
            id=row[0],
            person_id=row[1],
            source_entity_id=row[2],
            target_entity_id=row[3],
            survivor_name=row[4],
            notification_text=row[5] or "",
            confidence=row[6],
            acknowledged=row[7],
            auto_merged_at=row[8],
        )
        for row in rows
    ]


async def acknowledge_auto_merge_async(
    cursor, *, suggestion_id: str
) -> bool:
    """Mark an auto-merge notification dismissed. Idempotent."""
    await cursor.execute(
        """
        UPDATE identity_merge_suggestions
           SET acknowledged = true
         WHERE id = %s
           AND status = 'auto_merged'
        """,
        (suggestion_id,),
    )
    return cursor.rowcount > 0


async def unmerge_async(
    cursor,
    *,
    suggestion_id: str,
    push_embedding: Callable[..., str] | None,
    embedding_model: str,
    embedding_model_version: str,
) -> UnmergeResponse | None:
    """Reverse an auto-merge (or approved merge), per the 2026-06-06 design.

    The survivor keeps its blended description, but the labels this merge
    folded into its aliases are stripped back out, and the suggestion row
    is repointed to the resurrected entity's id. Unmerge is the user
    saying "these are different" — leaving the alias evidence in place
    (or leaving the dedup gate keyed to the dead source id) let the next
    scan re-detect and silently re-auto-merge the exact pair the user
    just pulled apart. The merged-away entity is resurrected as a
    brand-new active entity; the edges that were repointed onto the
    survivor are moved back to it, and edges deleted as duplicates are
    re-created on it. Returns ``None`` if the suggestion is not in a
    reversible state.
    """
    await cursor.execute(
        """
        SELECT person_id::text, source_entity_id::text,
               target_entity_id::text, undo_snapshot, proposed_alias
          FROM identity_merge_suggestions
         WHERE id = %s
           AND status IN ('auto_merged', 'approved')
         FOR UPDATE
        """,
        (suggestion_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    person_id, source_id, target_id, snapshot, proposed_alias = row
    if not snapshot:
        return None

    source_row = snapshot.get("source_row") or {}
    repointed_ids = snapshot.get("repointed_edge_ids") or []
    deleted_edges = snapshot.get("deleted_edges") or []

    await cursor.execute(
        """
        SELECT name, aliases
          FROM entities
         WHERE id = %s
         FOR UPDATE
        """,
        (target_id,),
    )
    survivor = await cursor.fetchone()
    survivor_name, survivor_aliases = survivor if survivor else ("", [])

    # 1. Resurrect the merged-away entity as a FRESH active entity. Its
    #    aliases drop the survivor's name — alias↔name overlap in either
    #    direction is what forms scan candidates.
    await cursor.execute(
        """
        INSERT INTO entities
              (person_id, kind, name, description, aliases, attributes,
               generation_prompt)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            person_id,
            source_row.get("kind"),
            source_row.get("name"),
            source_row.get("description"),
            [
                alias
                for alias in (source_row.get("aliases") or [])
                if _norm(alias) != _norm(survivor_name)
            ],
            Json(source_row.get("attributes") or {}),
            source_row.get("generation_prompt"),
        ),
    )
    new_entity_id = (await cursor.fetchone())[0]

    # 1b. Strip the labels this merge folded into the survivor's aliases
    #     (the source's name/aliases and the proposed alias).
    folded_labels = {
        _norm(source_row.get("name")),
        _norm(proposed_alias),
        *(_norm(alias) for alias in source_row.get("aliases") or []),
    }
    folded_labels.discard("")
    kept_aliases = [
        alias
        for alias in (survivor_aliases or [])
        if _norm(alias) not in folded_labels
    ]
    if kept_aliases != list(survivor_aliases or []):
        await cursor.execute(
            """
            UPDATE entities
               SET aliases = %s
             WHERE id = %s
            """,
            (kept_aliases, target_id),
        )

    # 2. Move the repointed edges off the survivor back onto the new entity.
    if repointed_ids:
        await cursor.execute(
            """
            UPDATE edges
               SET from_id = %s
             WHERE id = ANY(%s)
               AND from_kind = 'entity'
               AND from_id = %s
            """,
            (new_entity_id, repointed_ids, target_id),
        )
        await cursor.execute(
            """
            UPDATE edges
               SET to_id = %s
             WHERE id = ANY(%s)
               AND to_kind = 'entity'
               AND to_id = %s
            """,
            (new_entity_id, repointed_ids, target_id),
        )

    # 3. Re-create the deleted duplicate edges on the new entity (the
    #    survivor keeps its own equivalent copy).
    for e in deleted_edges:
        from_id = new_entity_id if (e["from_kind"] == "entity" and e["from_id"] == source_id) else e["from_id"]
        to_id = new_entity_id if (e["to_kind"] == "entity" and e["to_id"] == source_id) else e["to_id"]
        await cursor.execute(
            """
            INSERT INTO edges (from_kind, from_id, to_kind, to_id,
                               edge_type, attributes)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (from_kind, from_id, to_kind, to_id, edge_type)
            DO NOTHING
            """,
            (
                e["from_kind"],
                from_id,
                e["to_kind"],
                to_id,
                e["edge_type"],
                Json(e.get("attributes") or {}),
            ),
        )

    # 4. Mark the suggestion reversed and repoint it at the resurrected
    #    entity. The scanner's dedup gate is keyed on live entity ids, so
    #    the row only keeps suppressing re-detection of this pair if it
    #    follows the resurrection (the original source row stays a
    #    'merged' tombstone with a dead id). The original id is kept in
    #    the snapshot for audit.
    snapshot = dict(snapshot)
    snapshot["original_source_entity_id"] = source_id
    snapshot["resurrected_entity_id"] = new_entity_id
    await cursor.execute(
        """
        UPDATE identity_merge_suggestions
           SET status = 'unmerged',
               unmerged_at = now(),
               source_entity_id = %s,
               undo_snapshot = %s
         WHERE id = %s
        """,
        (new_entity_id, Json(snapshot), suggestion_id),
    )

    # 5. Re-embed the resurrected entity's description.
    if push_embedding is not None and source_row.get("description"):
        push_embedding(
            record_type="entity",
            record_id=new_entity_id,
            source_text=source_row.get("description"),
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )

    return UnmergeResponse(
        suggestion_id=suggestion_id,
        person_id=person_id,
        survivor_entity_id=target_id,
        resurrected_entity_id=new_entity_id,
        status="unmerged",
    )


async def _lock_pending_suggestion(cursor, *, suggestion_id: str):
    await cursor.execute(
        """
        SELECT person_id::text, source_entity_id::text, target_entity_id::text,
               proposed_alias
          FROM identity_merge_suggestions
         WHERE id = %s
           AND status = 'pending'
         FOR UPDATE
        """,
        (suggestion_id,),
    )
    return await cursor.fetchone()


async def _merge_entity_rows(
    cursor,
    *,
    person_id: str,
    source_id: str,
    target_id: str,
    proposed_alias: str | None,
) -> dict:
    """Merge ``source`` into ``target`` and return an undo snapshot.

    The snapshot captures everything unmerge needs to resurrect the
    source as a fresh standalone entity without disturbing the survivor:
      * ``source_row`` — the source entity's scalar fields.
      * ``repointed_edge_ids`` — edges moved from source onto the survivor
        (their PKs are stable through the UPDATE).
      * ``deleted_edges`` — source edges dropped because the survivor
        already had an equivalent (re-created on the resurrected entity at
        unmerge time).
    """
    await cursor.execute(
        """
        SELECT kind, name, description, aliases, attributes, generation_prompt
          FROM entities
         WHERE id = %s
           AND person_id = %s
           AND status = 'active'
         FOR UPDATE
        """,
        (source_id, person_id),
    )
    source = await cursor.fetchone()
    await cursor.execute(
        """
        SELECT name, description, aliases
          FROM entities
         WHERE id = %s
           AND person_id = %s
           AND status = 'active'
         FOR UPDATE
        """,
        (target_id, person_id),
    )
    target = await cursor.fetchone()
    if source is None or target is None:
        raise ValueError("source and target entities must both be active")

    source_kind, source_name, source_description, source_aliases, source_attributes, source_generation_prompt = source
    target_name, target_description, target_aliases = target
    aliases = _merge_aliases(
        target_name=target_name,
        existing=target_aliases or [],
        additions=[
            source_name,
            *(source_aliases or []),
            proposed_alias or "",
        ],
    )
    description = _merge_description(target_description, source_description)

    repointed_ids, deleted_edges = await _repoint_entity_edges(
        cursor, old_id=source_id, new_id=target_id
    )
    await cursor.execute(
        """
        UPDATE entities
           SET aliases = %s,
               description = %s,
               description_embedding = NULL,
               embedding_model = NULL,
               embedding_model_version = NULL
         WHERE id = %s
        """,
        (aliases, description, target_id),
    )
    await cursor.execute(
        """
        UPDATE entities
           SET status = 'merged',
               merged_into = %s
         WHERE id = %s
        """,
        (target_id, source_id),
    )

    return {
        "source_row": {
            "person_id": person_id,
            "kind": source_kind,
            "name": source_name,
            "description": source_description,
            "aliases": list(source_aliases or []),
            "attributes": source_attributes or {},
            "generation_prompt": source_generation_prompt,
        },
        "repointed_edge_ids": repointed_ids,
        "deleted_edges": deleted_edges,
    }


async def _repoint_entity_edges(
    cursor, *, old_id: str, new_id: str
) -> tuple[list[str], list[dict]]:
    """Repoint ``old_id``'s edges onto ``new_id``; return undo provenance.

    Returns ``(repointed_edge_ids, deleted_edges)``:
      * ``repointed_edge_ids`` — PKs of edges that survived (UPDATEd from
        ``old_id`` to ``new_id``). PKs are stable across the UPDATE, so
        unmerge can move exactly these back.
      * ``deleted_edges`` — full rows of source edges removed because the
        survivor already had an equivalent edge (UNIQUE collision).
    """
    repointed_ids: list[str] = []
    deleted_edges: list[dict] = []
    for direction in ("to", "from"):
        kind_col = f"{direction}_kind"
        id_col = f"{direction}_id"
        await cursor.execute(
            f"""
            DELETE FROM edges old
             WHERE old.{kind_col} = 'entity'
               AND old.{id_col} = %(old)s
               AND EXISTS (
                 SELECT 1
                   FROM edges new
                  WHERE new.from_kind = CASE
                          WHEN %(direction)s = 'from' THEN 'entity'
                          ELSE old.from_kind
                        END
                    AND new.from_id = CASE
                          WHEN %(direction)s = 'from' THEN %(new)s::uuid
                          ELSE old.from_id
                        END
                    AND new.to_kind = CASE
                          WHEN %(direction)s = 'to' THEN 'entity'
                          ELSE old.to_kind
                        END
                    AND new.to_id = CASE
                          WHEN %(direction)s = 'to' THEN %(new)s::uuid
                          ELSE old.to_id
                        END
                    AND new.edge_type = old.edge_type
               )
         RETURNING old.from_kind, old.from_id::text, old.to_kind,
                   old.to_id::text, old.edge_type, old.attributes
            """,
            {"old": old_id, "new": new_id, "direction": direction},
        )
        for row in await cursor.fetchall():
            deleted_edges.append(
                {
                    "from_kind": row[0],
                    "from_id": row[1],
                    "to_kind": row[2],
                    "to_id": row[3],
                    "edge_type": row[4],
                    "attributes": row[5] or {},
                }
            )
        await cursor.execute(
            f"""
            UPDATE edges
               SET {id_col} = %s
             WHERE {kind_col} = 'entity'
               AND {id_col} = %s
         RETURNING id::text
            """,
            (new_id, old_id),
        )
        repointed_ids.extend(row[0] for row in await cursor.fetchall())
    return repointed_ids, deleted_edges


async def _reject_sibling_suggestions(
    cursor,
    *,
    person_id: str,
    source_id: str,
    approved_suggestion_id: str,
) -> None:
    await cursor.execute(
        """
        UPDATE identity_merge_suggestions
           SET status = 'rejected',
               rejected_at = now()
         WHERE person_id = %s
           AND source_entity_id = %s
           AND id <> %s
           AND status = 'pending'
        """,
        (person_id, source_id, approved_suggestion_id),
    )


async def _target_source_text(cursor, *, target_id: str) -> str:
    await cursor.execute(
        """
        SELECT description
          FROM entities
         WHERE id = %s
        """,
        (target_id,),
    )
    row = await cursor.fetchone()
    return str(row[0] or "") if row else ""


def _merge_aliases(
    *,
    target_name: str,
    existing: list[str],
    additions: list[str],
) -> list[str]:
    seen = {target_name.strip().lower()}
    aliases: list[str] = []
    for raw in [*existing, *additions]:
        alias = raw.strip()
        if not alias:
            continue
        key = alias.lower()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(alias)
    return aliases


def _merge_description(target_description: str | None, source_description: str | None) -> str | None:
    target = _clean_description(target_description)
    source = _clean_description(source_description)
    if not source:
        return target
    if not target:
        return source
    if _norm(source) in _norm(target):
        return target
    if _norm(target) in _norm(source):
        return source
    return f"{target} {source}"


def _clean_description(value: str | None) -> str | None:
    cleaned = " ".join((value or "").split())
    return cleaned or None
