"""Persistence helpers for user-approved entity merges."""

from __future__ import annotations

from typing import Callable

import structlog

from .schema import IdentityMergeActionResponse, IdentityMergeSuggestion

log = structlog.get_logger("flashback.identity_merges")


def create_entity_merge_suggestions(
    cursor,
    *,
    person_id: str,
    target_entity_ids: list[str],
) -> list[str]:
    """
    Create pending suggestions when a newly inserted target entity appears to
    identify or duplicate another active entity.

    Example: a target entity has an alias matching an already-active source
    entity name. We suggest source -> target. We also catch the common LLM
    shape where that old label lands in the target description instead of
    ``aliases``.
    """

    suggestion_ids: list[str] = []
    for target_id in target_entity_ids:
        cursor.execute(
            """
            SELECT id::text, kind, name, COALESCE(description, ''), aliases
              FROM entities
             WHERE id = %s
               AND person_id = %s
               AND status = 'active'
            """,
            (target_id, person_id),
        )
        row = cursor.fetchone()
        if row is None:
            continue
        _target_id, target_kind, target_name, target_description, aliases = row
        labels = [a.strip() for a in (aliases or []) if a and a.strip()]

        cursor.execute(
            """
            SELECT id::text, name
              FROM entities
             WHERE person_id = %s
               AND status = 'active'
               AND id <> %s
               AND kind = %s
            """,
            (person_id, target_id, target_kind),
        )
        seen_sources: set[str] = set()
        for source_id, source_name in cursor.fetchall():
            match_label = _merge_match_label(
                source_name=source_name,
                target_name=target_name,
                target_description=target_description,
                aliases=labels,
            )
            if match_label is None or source_id in seen_sources:
                continue
            seen_sources.add(source_id)
            inserted_id = _insert_suggestion(
                cursor,
                person_id=person_id,
                source_id=source_id,
                target_id=target_id,
                proposed_alias=match_label,
                reason=_suggestion_reason(
                    source_name=source_name,
                    target_name=target_name,
                    match_label=match_label,
                ),
            )
            if inserted_id:
                suggestion_ids.append(inserted_id)
    if suggestion_ids:
        log.info(
            "identity_merge.suggestions_created",
            person_id=person_id,
            count=len(suggestion_ids),
        )
    return suggestion_ids


def _merge_match_label(
    *,
    source_name: str,
    target_name: str,
    target_description: str,
    aliases: list[str],
) -> str | None:
    source_norm = _norm(source_name)
    target_norm = _norm(target_name)
    if not source_norm:
        return None
    if source_norm == target_norm:
        return source_name
    if source_norm in {_norm(alias) for alias in aliases}:
        return source_name
    if source_norm in _norm(target_description):
        return source_name
    return None


def _insert_suggestion(
    cursor,
    *,
    person_id: str,
    source_id: str,
    target_id: str,
    proposed_alias: str,
    reason: str,
) -> str | None:
    cursor.execute(
        """
        INSERT INTO identity_merge_suggestions
              (person_id, source_entity_id, target_entity_id,
               proposed_alias, reason, source)
        SELECT %s, %s, %s, %s, %s, 'extraction'
         WHERE NOT EXISTS (
               SELECT 1
                 FROM identity_merge_suggestions
                WHERE person_id = %s
                  AND (
                       (source_entity_id = %s AND target_entity_id = %s)
                    OR (source_entity_id = %s AND target_entity_id = %s)
                  )
         )
        ON CONFLICT (person_id, source_entity_id, target_entity_id)
        WHERE status = 'pending'
        DO NOTHING
        RETURNING id::text
        """,
        (
            person_id,
            source_id,
            target_id,
            proposed_alias,
            reason,
            person_id,
            source_id,
            target_id,
            target_id,
            source_id,
        ),
    )
    inserted = cursor.fetchone()
    return inserted[0] if inserted else None


def _suggestion_reason(
    *,
    source_name: str,
    target_name: str,
    match_label: str,
) -> str:
    if _norm(source_name) == _norm(target_name):
        return (
            f"Extraction created another active entity named {target_name!r}; "
            f"existing entity {source_name!r} may be the same identity."
        )
    return (
        f"Extraction treated {match_label!r} as an alias or description for "
        f"{target_name!r}; existing entity {source_name!r} matches that label."
    )


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
               s.target_entity_id, tgt.name AS target_entity_name,
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
            target_entity_id=row[4],
            target_entity_name=row[5],
            proposed_alias=row[6],
            reason=row[7],
            source=row[8],
            status=row[9],
            created_at=row[10],
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

    await _merge_entity_rows(
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
               approved_at = now()
         WHERE id = %s
        """,
        (suggestion_id,),
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
) -> None:
    await cursor.execute(
        """
        SELECT name, description, aliases
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

    source_name, source_description, source_aliases = source
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

    await _repoint_entity_edges(cursor, old_id=source_id, new_id=target_id)
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


async def _repoint_entity_edges(cursor, *, old_id: str, new_id: str) -> None:
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
            """,
            {"old": old_id, "new": new_id, "direction": direction},
        )
        await cursor.execute(
            f"""
            UPDATE edges
               SET {id_col} = %s
             WHERE {kind_col} = 'entity'
               AND {id_col} = %s
            """,
            (new_id, old_id),
        )


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
    if not source_description:
        return target_description
    if not target_description:
        return source_description
    if source_description.lower() in target_description.lower():
        return target_description
    return f"{target_description} Also known from earlier context as: {source_description}"
