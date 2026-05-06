"""Async DB helpers for node_edits.

The HTTP service uses an async psycopg pool; the extraction worker's
persistence layer is sync. Rather than wrap sync calls in
``asyncio.to_thread`` (which would also need a separate sync pool), we
port the small set of SQL helpers we need to async here.

Every helper is a one-for-one port of the corresponding sync function
in :mod:`flashback.workers.extraction.persistence` or
:mod:`flashback.identity_merges.repository`. The SQL is identical;
only the cursor calls are awaited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from psycopg.types.json import Json

from flashback.db.edges import validate_edge
from flashback.workers.extraction.persistence import (
    LLMProvenance,
    PersonRow,
)
from flashback.workers.extraction.schema import ExtractedEntity, ExtractedMoment

log = structlog.get_logger("flashback.node_edits._async_sql")


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def fetch_person_async(cur, *, person_id: str) -> PersonRow | None:
    """Look up the legacy subject. Used by the subject guard.

    Mirrors :func:`flashback.workers.extraction.persistence.fetch_person`
    one-for-one. ``persons`` does not currently carry an ``aliases``
    column; we expose an empty list so the subject guard signature is
    stable across schema additions.
    """
    await cur.execute(
        """
        SELECT id::text, name
          FROM persons
         WHERE id = %s
        """,
        (person_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    pid, name = row
    return PersonRow(id=pid, name=name, aliases=[])


async def fetch_active_moment_async(
    cur,
    *,
    moment_id: str,
    person_id: str,
) -> dict[str, Any] | None:
    """Fetch the columns we expose to the edit-LLM as the prior row.

    Returns ``None`` if the moment is missing, belongs to a different
    person, or is not status='active' (a superseded row cannot be
    edited; fork from the current canonical version).
    """
    await cur.execute(
        """
        SELECT id::text, person_id::text, title, narrative,
               time_anchor, life_period_estimate, sensory_details,
               emotional_tone, contributor_perspective,
               generation_prompt
          FROM moments
         WHERE id = %s
           AND person_id = %s
           AND status = 'active'
        """,
        (moment_id, person_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "person_id": row[1],
        "title": row[2],
        "narrative": row[3],
        "time_anchor": row[4],
        "life_period_estimate": row[5],
        "sensory_details": row[6],
        "emotional_tone": row[7],
        "contributor_perspective": row[8],
        "generation_prompt": row[9],
    }


async def fetch_active_entity_async(
    cur,
    *,
    entity_id: str,
    person_id: str,
) -> dict[str, Any] | None:
    """Fetch the columns we expose to the edit-LLM as the prior row."""
    await cur.execute(
        """
        SELECT id::text, person_id::text, kind, name, description,
               aliases, attributes, generation_prompt
          FROM entities
         WHERE id = %s
           AND person_id = %s
           AND status = 'active'
        """,
        (entity_id, person_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "person_id": row[1],
        "kind": row[2],
        "name": row[3],
        "description": row[4],
        "aliases": row[5] or [],
        "attributes": row[6] or {},
        "generation_prompt": row[7],
    }


# ---------------------------------------------------------------------------
# Subject guard (pure)
# ---------------------------------------------------------------------------


def apply_subject_guard_pure(
    *,
    person: PersonRow,
    entities: list[ExtractedEntity],
) -> tuple[list[ExtractedEntity], int]:
    """Drop entities whose name or aliases collide with the legacy subject.

    Pure mirror of
    :func:`flashback.workers.extraction.persistence._apply_subject_guard`.
    """
    forbidden = {person.name.strip().lower()}
    for alias in person.aliases or []:
        if alias:
            forbidden.add(alias.strip().lower())

    surviving: list[ExtractedEntity] = []
    dropped = 0
    for entity in entities:
        names = {entity.name.strip().lower()}
        for alias in entity.aliases:
            if alias:
                names.add(alias.strip().lower())
        if names & forbidden:
            log.warning(
                "node_edits.subject_self_reference_dropped",
                entity_name=entity.name,
                subject_name=person.name,
            )
            dropped += 1
            continue
        surviving.append(entity)
    return surviving, dropped


def build_entity_index_map(
    *,
    original_entities: list[ExtractedEntity],
    surviving_entities: list[ExtractedEntity],
    surviving_ids: list[str],
) -> dict[int, str | None]:
    """Map original-index -> inserted UUID (or ``None`` if guard-dropped)."""
    surviving_by_id = {
        id(e): uid for e, uid in zip(surviving_entities, surviving_ids)
    }
    out: dict[int, str | None] = {}
    for orig_idx, entity in enumerate(original_entities):
        out[orig_idx] = surviving_by_id.get(id(entity))
    return out


# ---------------------------------------------------------------------------
# Inserts
# ---------------------------------------------------------------------------


async def insert_entities_async(
    cur,
    *,
    person_id: str,
    entities: list[ExtractedEntity],
    llm_provenance: LLMProvenance | None,
) -> list[str]:
    ids: list[str] = []
    for e in entities:
        await cur.execute(
            """
            INSERT INTO entities
                  (person_id, kind, name, description, aliases,
                   attributes, generation_prompt,
                   llm_provider, llm_model, prompt_version)
            VALUES (%s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s)
            RETURNING id::text
            """,
            (
                person_id,
                e.kind,
                e.name,
                e.description,
                list(e.aliases),
                Json(e.attributes or {}),
                e.generation_prompt,
                llm_provenance.provider if llm_provenance else None,
                llm_provenance.model if llm_provenance else None,
                llm_provenance.prompt_version if llm_provenance else None,
            ),
        )
        row = await cur.fetchone()
        ids.append(row[0])
    return ids


async def insert_moment_async(
    cur,
    *,
    person_id: str,
    moment: ExtractedMoment,
    llm_provenance: LLMProvenance | None,
) -> str:
    time_anchor_payload: Any = None
    if moment.time_anchor is not None:
        ta = moment.time_anchor.model_dump(exclude_none=True)
        time_anchor_payload = Json(ta) if ta else None

    await cur.execute(
        """
        INSERT INTO moments
              (person_id, title, narrative, time_anchor,
               life_period_estimate, sensory_details, emotional_tone,
               contributor_perspective, generation_prompt,
               llm_provider, llm_model, prompt_version)
        VALUES (%s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s)
        RETURNING id::text
        """,
        (
            person_id,
            moment.title,
            moment.narrative,
            time_anchor_payload,
            moment.life_period_estimate,
            moment.sensory_details,
            moment.emotional_tone,
            moment.contributor_perspective,
            moment.generation_prompt,
            llm_provenance.provider if llm_provenance else None,
            llm_provenance.model if llm_provenance else None,
            llm_provenance.prompt_version if llm_provenance else None,
        ),
    )
    row = await cur.fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


async def insert_edge_async(
    cur,
    *,
    from_kind: str,
    from_id: str,
    to_kind: str,
    to_id: str,
    edge_type: str,
    attributes: dict | None = None,
) -> bool:
    """Single validated edge insert. Returns True if a row was inserted."""
    validate_edge(from_kind, to_kind, edge_type)
    await cur.execute(
        """
        INSERT INTO edges (from_kind, from_id, to_kind, to_id,
                           edge_type, attributes)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (from_kind, from_id, to_kind, to_id, edge_type)
        DO NOTHING
        RETURNING id
        """,
        (
            from_kind,
            from_id,
            to_kind,
            to_id,
            edge_type,
            Json(attributes or {}),
        ),
    )
    row = await cur.fetchone()
    return row is not None


async def insert_moment_edges_async(
    cur,
    *,
    moment_id: str,
    moment: ExtractedMoment,
    entity_index_to_id: dict[int, str | None],
    entity_kinds: list[str],
) -> int:
    """Mirror of
    :func:`flashback.workers.extraction.persistence._insert_moment_edges`,
    minus exemplifies (no traits in an edit). Returns the count of new
    edges actually inserted (ON CONFLICT skips repeat tuples).
    """
    inserted = 0
    for idx in moment.involves_entity_indexes:
        target_id = entity_index_to_id.get(idx)
        if target_id is None:
            continue
        if await insert_edge_async(
            cur,
            from_kind="moment",
            from_id=moment_id,
            to_kind="entity",
            to_id=target_id,
            edge_type="involves",
        ):
            inserted += 1

    if moment.happened_at_entity_index is not None:
        idx = moment.happened_at_entity_index
        target_id = entity_index_to_id.get(idx)
        if target_id is not None and 0 <= idx < len(entity_kinds):
            if entity_kinds[idx] != "place":
                log.warning(
                    "node_edits.happened_at_not_place_dropped",
                    moment_id=moment_id,
                    target_kind=entity_kinds[idx],
                )
            else:
                if await insert_edge_async(
                    cur,
                    from_kind="moment",
                    from_id=moment_id,
                    to_kind="entity",
                    to_id=target_id,
                    edge_type="happened_at",
                ):
                    inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupersedeCounts:
    inbound_repointed: int
    outbound_deleted: int


async def supersede_moment_async(
    cur,
    *,
    old_moment_id: str,
    new_moment_id: str,
) -> SupersedeCounts:
    """Async port of
    :func:`flashback.workers.extraction.persistence._supersede_moment`.

    Flips status, repoints inbound edges (deleting any that would
    collide with edges already pointing at the new moment), and deletes
    outbound edges from the old moment so the caller's fresh outbound
    edges don't trip the UNIQUE constraint.
    """
    await cur.execute(
        """
        UPDATE moments
           SET status = 'superseded',
               superseded_by = %s
         WHERE id = %s
           AND status = 'active'
        """,
        (new_moment_id, old_moment_id),
    )

    # Delete inbound that would collide with already-existing edges
    # pointing at the new moment.
    await cur.execute(
        """
        DELETE FROM edges old
         WHERE old.to_kind = 'moment'
           AND old.to_id   = %(old)s
           AND EXISTS (
             SELECT 1
               FROM edges new
              WHERE new.from_kind = old.from_kind
                AND new.from_id   = old.from_id
                AND new.to_kind   = 'moment'
                AND new.to_id     = %(new)s
                AND new.edge_type = old.edge_type
           )
        """,
        {"old": old_moment_id, "new": new_moment_id},
    )
    await cur.execute(
        """
        UPDATE edges
           SET to_id = %s
         WHERE to_kind = 'moment'
           AND to_id   = %s
        """,
        (new_moment_id, old_moment_id),
    )
    inbound_repointed = cur.rowcount or 0

    await cur.execute(
        """
        DELETE FROM edges
         WHERE from_kind = 'moment'
           AND from_id   = %s
        """,
        (old_moment_id,),
    )
    outbound_deleted = cur.rowcount or 0

    return SupersedeCounts(
        inbound_repointed=int(inbound_repointed),
        outbound_deleted=int(outbound_deleted),
    )


# ---------------------------------------------------------------------------
# Entity merge suggestions (async port)
# ---------------------------------------------------------------------------


async def create_entity_merge_suggestions_async(
    cur,
    *,
    person_id: str,
    target_entity_ids: list[str],
) -> list[str]:
    """Async port of
    :func:`flashback.identity_merges.repository.create_entity_merge_suggestions`.
    """
    suggestion_ids: list[str] = []
    for target_id in target_entity_ids:
        await cur.execute(
            """
            SELECT id::text, kind, name, COALESCE(description, ''), aliases
              FROM entities
             WHERE id = %s
               AND person_id = %s
               AND status = 'active'
            """,
            (target_id, person_id),
        )
        row = await cur.fetchone()
        if row is None:
            continue
        _target_id, target_kind, target_name, target_description, aliases = row
        labels = [a.strip() for a in (aliases or []) if a and a.strip()]

        await cur.execute(
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
        candidates = await cur.fetchall()
        seen_sources: set[str] = set()
        for source_id, source_name in candidates:
            match_label = _merge_match_label(
                source_name=source_name,
                target_name=target_name,
                target_description=target_description,
                aliases=labels,
            )
            if match_label is None or source_id in seen_sources:
                continue
            seen_sources.add(source_id)
            inserted_id = await _insert_suggestion_async(
                cur,
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


def _suggestion_reason(
    *,
    source_name: str,
    target_name: str,
    match_label: str,
) -> str:
    if _norm(source_name) == _norm(target_name):
        return (
            f"Edit-refinement created another active entity named "
            f"{target_name!r}; existing entity {source_name!r} may be the "
            f"same identity."
        )
    return (
        f"Edit-refinement treated {match_label!r} as an alias or "
        f"description for {target_name!r}; existing entity "
        f"{source_name!r} matches that label."
    )


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


async def _insert_suggestion_async(
    cur,
    *,
    person_id: str,
    source_id: str,
    target_id: str,
    proposed_alias: str,
    reason: str,
) -> str | None:
    await cur.execute(
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
    inserted = await cur.fetchone()
    return inserted[0] if inserted else None


# ---------------------------------------------------------------------------
# In-place entity update
# ---------------------------------------------------------------------------


async def update_entity_in_place_async(
    cur,
    *,
    entity_id: str,
    person_id: str,
    description: str | None,
    aliases: list[str],
    attributes: dict[str, Any],
    generation_prompt: str | None,
    llm_provenance: LLMProvenance | None,
) -> bool:
    """UPDATE the entity row in place.

    Mirrors the survivor-update pattern in
    :func:`flashback.identity_merges.repository._merge_entity_rows`:
    rewrite content fields and clear the embedding columns so the
    embedding-worker version-guarded UPDATE will accept the fresh
    embedding pushed afterward. Caller is responsible for the embedding
    queue push.

    Returns True if a row was updated, False if the entity wasn't
    active under that ``(id, person_id)`` (lost-update guard).
    """
    await cur.execute(
        """
        UPDATE entities
           SET description = %s,
               aliases = %s,
               attributes = %s,
               generation_prompt = COALESCE(%s, generation_prompt),
               description_embedding = NULL,
               embedding_model = NULL,
               embedding_model_version = NULL,
               llm_provider = %s,
               llm_model = %s,
               prompt_version = %s,
               updated_at = now()
         WHERE id = %s
           AND person_id = %s
           AND status = 'active'
        """,
        (
            description,
            list(aliases),
            Json(attributes or {}),
            generation_prompt,
            llm_provenance.provider if llm_provenance else None,
            llm_provenance.model if llm_provenance else None,
            llm_provenance.prompt_version if llm_provenance else None,
            entity_id,
            person_id,
        ),
    )
    return (cur.rowcount or 0) > 0


__all__ = [
    "SupersedeCounts",
    "apply_subject_guard_pure",
    "build_entity_index_map",
    "create_entity_merge_suggestions_async",
    "fetch_active_entity_async",
    "fetch_active_moment_async",
    "fetch_person_async",
    "insert_edge_async",
    "insert_entities_async",
    "insert_moment_async",
    "insert_moment_edges_async",
    "supersede_moment_async",
    "update_entity_in_place_async",
]
