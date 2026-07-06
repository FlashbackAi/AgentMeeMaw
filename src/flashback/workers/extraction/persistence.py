"""
Persistence layer for the Extraction Worker.

The contract: take a validated :class:`ExtractionResult`, plus refinement
decisions, plus the original :class:`ExtractionMessage` payload, and
write everything to Postgres atomically.

Order inside the single transaction:

  1. Apply the **subject guard** to drop any extracted entity that
     matches the legacy subject's name or aliases.
  2. Insert ENTITIES; build an ``index → UUID`` map.
  3. Insert TRAITS; build an ``index → UUID`` map.
  4. Insert MOMENTS (referenced entities and traits already have UUIDs).
  5. For moments with a ``supersedes_id``: mark the old moment as
     ``superseded`` and repoint all inbound edges to the new id;
     delete outbound edges from the old moment (they are recreated for
     the new moment in step 6).
  6. Insert EDGES — every edge validated by ``validate_edge`` first.
  7. Insert DROPPED_REFERENCE questions.
  8. Insert ``answered_by`` edges from ``seeded_question_id`` to each
     new moment, if a seeding question was provided.

If anything raises, the worker rolls back the surrounding transaction
and does not ack the SQS message. SQS visibility timeout will redrive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import structlog
from psycopg.types.json import Json

from flashback.db.edges import validate_edge
from flashback.storybook.collections import TAGGABLE_SLUGS

from .schema import ExtractedEntity, ExtractedMoment, ExtractionResult

log = structlog.get_logger("flashback.workers.extraction.persistence")

# Frozen set of grid-collection slugs the extraction tagger may assign; used to
# drop unknown/invented slugs before they hit the moments column.
_TAGGABLE_SLUG_SET: frozenset[str] = frozenset(TAGGABLE_SLUGS)


# ---------------------------------------------------------------------------
# Inputs and result
# ---------------------------------------------------------------------------


@dataclass
class MomentDecision:
    """Refinement decisions attached to a single new moment.

    ``supersedes_id`` is the existing moment this new one should refine
    (set when the compatibility LLM returned ``refinement``). At most
    one supersession per new moment.

    ``contradicts_ids`` is the (possibly empty) list of existing moments
    the new one is in factual conflict with. Both rows are preserved;
    we currently log the conflict and stop there (a future step will
    surface it for review).
    """

    moment: ExtractedMoment
    supersedes_id: str | None = None
    contradicts_ids: list[str] = field(default_factory=list)


@dataclass
class PersonRow:
    id: str
    name: str
    aliases: list[str]
    gender: str | None = None
    relationship: str | None = None
    contributor_gender: str | None = None


@dataclass
class EntityWriteResult:
    """Outcome of persisting one extracted entity (prevention layer 1).

    ``reused`` is True when the extracted entity resolved to an existing
    active same-kind row (by normalized name) instead of inserting a new
    one — the source of the duplicate-entity flood. Reused entities get
    NO new artifact job. ``embed_text`` is the text to (re)embed, or
    ``None`` to skip the embedding job (e.g. a reused entity whose
    description did not change).
    """

    id: str
    reused: bool
    description_changed: bool
    embed_text: str | None


@dataclass
class PersistenceResult:
    """What the worker needs after a successful commit."""

    moment_ids: list[str]
    entity_ids: list[str]
    surviving_entities: list[ExtractedEntity]
    entity_writes: list["EntityWriteResult"]
    trait_ids: list[str]
    question_ids: list[str]
    superseded_moment_ids: list[str]
    merge_suggestion_ids: list[str]
    dropped_entities_count: int

    # Per-moment booleans surfaced to the Coverage Tracker so it doesn't
    # have to re-derive them from the graph.
    moment_signals: list["MomentCoverageSignal"]


@dataclass
class MomentCoverageSignal:
    has_sensory: bool
    has_voice: bool  # trait alongside, OR linked person entity has saying/mannerism
    has_place: bool
    has_non_subject_person: bool
    has_era: bool


@dataclass(frozen=True)
class LLMProvenance:
    provider: str
    model: str
    prompt_version: str


@dataclass(frozen=True)
class TraitMergeResolution:
    """Pre-computed cross-session dedup resolution for a single trait.

    Set by the worker (outside the transaction) when an active trait
    with the same case-insensitive name already exists for this person.
    The worker has called the merge LLM and already replaced
    ``extraction.traits[i].description`` with the merged description,
    so outbox/embedding code reads the right text by default. Here we
    only need to flag that this index must UPDATE the existing row
    rather than INSERT a new one. Persistence:

      * UPDATEs the existing row's ``description`` to the (now-merged)
        value on the trait
      * NULLs ``description_embedding`` + ``embedding_model`` +
        ``embedding_model_version`` so the embedding worker re-embeds
        on the merged description
      * Returns ``existing_trait_id`` at this index so moment
        ``exemplifies`` edges resolve to the existing trait rather
        than a duplicate row
    """

    existing_trait_id: str


@dataclass(frozen=True)
class ExistingTraitRow:
    id: str
    name: str
    description: str | None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def persist_extraction(
    cursor,
    *,
    person: PersonRow,
    extraction: ExtractionResult,
    moment_decisions: list[MomentDecision],
    seeded_question_id: str | None = None,
    seeded_question_ids: list[str] | None = None,
    llm_provenance: LLMProvenance | None = None,
    trait_merge_resolutions: list[TraitMergeResolution | None] | None = None,
    theme_slug_to_id: dict[str, str] | None = None,
    entity_description_overrides: dict[tuple[str, str], str] | None = None,
) -> PersistenceResult:
    """Run the full transactional write. Caller owns BEGIN/COMMIT/ROLLBACK.

    ``theme_slug_to_id`` is the active {slug: theme_id} map for this
    person, captured by the worker just before the LLM call. For each
    moment, the LLM-emitted ``themes`` slugs are resolved against this
    map and ``themed_as`` edges are written. Slugs not present in the
    map are dropped silently (under-extract per invariant #6).
    """

    if len(moment_decisions) != len(extraction.moments):
        raise ValueError(
            "moment_decisions length must match extraction.moments length"
        )
    if (
        trait_merge_resolutions is not None
        and len(trait_merge_resolutions) != len(extraction.traits)
    ):
        raise ValueError(
            "trait_merge_resolutions length must match extraction.traits length"
        )

    surviving_entities, dropped_count = _apply_subject_guard(
        person=person, entities=extraction.entities
    )

    entity_writes = _persist_entities(
        cursor,
        person_id=person.id,
        entities=surviving_entities,
        llm_provenance=llm_provenance,
        description_overrides=entity_description_overrides,
    )
    entity_ids = [w.id for w in entity_writes]
    trait_ids = _insert_traits(
        cursor,
        person_id=person.id,
        traits=extraction.traits,
        llm_provenance=llm_provenance,
        merge_resolutions=trait_merge_resolutions,
    )

    # Map original-index -> UUID, accounting for entities that were dropped
    # by the subject guard. Dropped entities map to ``None``, which causes
    # any moment referencing them to skip that edge cleanly.
    entity_index_to_id = _build_entity_index_map(
        original_entities=extraction.entities,
        surviving_entities=surviving_entities,
        surviving_ids=entity_ids,
    )

    moment_ids: list[str] = []
    superseded_ids: list[str] = []
    moment_signals: list[MomentCoverageSignal] = []

    for decision in moment_decisions:
        moment_id = _insert_moment(
            cursor,
            person_id=person.id,
            moment=decision.moment,
            llm_provenance=llm_provenance,
        )
        moment_ids.append(moment_id)

        if decision.supersedes_id is not None:
            _supersede_moment(
                cursor,
                old_moment_id=decision.supersedes_id,
                new_moment_id=moment_id,
            )
            superseded_ids.append(decision.supersedes_id)

        for cid in decision.contradicts_ids:
            log.info(
                "extraction.contradiction_logged",
                new_moment_id=moment_id,
                existing_moment_id=cid,
            )

        _insert_moment_edges(
            cursor,
            moment_id=moment_id,
            moment=decision.moment,
            entity_index_to_id=entity_index_to_id,
            entity_kinds=[e.kind for e in surviving_entities],
            trait_ids=trait_ids,
        )
        _insert_themed_as_edges(
            cursor,
            moment_id=moment_id,
            theme_slugs=decision.moment.themes,
            theme_slug_to_id=theme_slug_to_id or {},
        )
        moment_signals.append(
            _coverage_signal_for(
                moment=decision.moment,
                entity_index_to_id=entity_index_to_id,
                surviving_entities=surviving_entities,
                has_traits_in_segment=bool(extraction.traits),
            )
        )

    _insert_entity_related_edges(
        cursor,
        surviving_entities=surviving_entities,
        entity_ids=entity_ids,
    )

    question_ids = _insert_dropped_reference_questions(
        cursor,
        person_id=person.id,
        dropped_references=extraction.dropped_references,
        llm_provenance=llm_provenance,
    )

    answered_question_ids = _dedupe_question_ids(seeded_question_id, seeded_question_ids)
    if answered_question_ids and moment_ids:
        for question_id in answered_question_ids:
            _insert_answered_by_edges(
                cursor,
                question_id=question_id,
                moment_ids=moment_ids,
            )

    # Identity reconciliation no longer runs inline as brute-force string
    # matching. Prevention layer 1 (``_persist_entities``) already collapses
    # same-name duplicates deterministically; anything subtler is handled
    # post-commit by the verifier-gated reconcile (see worker + scanner).
    return PersistenceResult(
        moment_ids=moment_ids,
        entity_ids=entity_ids,
        surviving_entities=list(surviving_entities),
        entity_writes=entity_writes,
        trait_ids=trait_ids,
        question_ids=question_ids,
        superseded_moment_ids=superseded_ids,
        merge_suggestion_ids=[],
        dropped_entities_count=dropped_count,
        moment_signals=moment_signals,
    )


def _dedupe_question_ids(
    seeded_question_id: str | None,
    seeded_question_ids: list[str] | None,
) -> list[str]:
    ids: list[str] = []
    if seeded_question_id:
        ids.append(seeded_question_id)
    ids.extend(seeded_question_ids or [])
    deduped: list[str] = []
    seen: set[str] = set()
    for question_id in ids:
        if question_id not in seen:
            deduped.append(question_id)
            seen.add(question_id)
    return deduped


# ---------------------------------------------------------------------------
# Subject guard
# ---------------------------------------------------------------------------


def _apply_subject_guard(
    *, person: PersonRow, entities: list[ExtractedEntity]
) -> tuple[list[ExtractedEntity], int]:
    """Drop entities whose name or aliases collide with the legacy subject."""
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
                "extraction.subject_self_reference_dropped",
                entity_name=entity.name,
                subject_name=person.name,
            )
            dropped += 1
            continue
        surviving.append(entity)
    return surviving, dropped


def _build_entity_index_map(
    *,
    original_entities: list[ExtractedEntity],
    surviving_entities: list[ExtractedEntity],
    surviving_ids: list[str],
) -> dict[int, str | None]:
    """
    Build a map from original entity index → inserted UUID (or ``None`` if
    the entity was dropped by the subject guard).

    We rely on object identity to find each surviving entity's original
    index; the subject guard preserves order and never mutates entities,
    so this is unambiguous.
    """
    surviving_by_id = {id(e): uid for e, uid in zip(surviving_entities, surviving_ids)}
    out: dict[int, str | None] = {}
    for orig_idx, entity in enumerate(original_entities):
        out[orig_idx] = surviving_by_id.get(id(entity))
    return out


# ---------------------------------------------------------------------------
# Inserts
# ---------------------------------------------------------------------------


def _persist_entities(
    cursor,
    *,
    person_id: str,
    entities: list[ExtractedEntity],
    llm_provenance: LLMProvenance | None,
    description_overrides: dict[tuple[str, str], str] | None = None,
) -> list["EntityWriteResult"]:
    """Persist entities, reusing an existing active row on a name match.

    Prevention layer 1 (CLAUDE.md invariant #17 / 2026-06-06 design §5.1):
    before inserting, look for an existing **active, same-kind** entity for
    this person whose normalized name matches the extracted name. On match,
    reuse that row instead of inserting a duplicate — fold new aliases in,
    fill an empty description, and skip the artifact job. This kills the
    same-name duplicate flood (e.g. a new "Ishita"/"Aarav"/"Comet" row every
    segment) at the source, deterministically, with no LLM.

    Applies to ALL entity kinds (person/place/object/organization). The
    rarer different-name identity is handled upstream by the extraction
    catalog and downstream by the verifier-gated reconcile.

    Returns one :class:`EntityWriteResult` per entity, in input order.
    """
    overrides = description_overrides or {}
    results: list[EntityWriteResult] = []
    for e in entities:
        existing = _find_existing_active_entity(
            cursor, person_id=person_id, kind=e.kind, name=e.name
        )
        if existing is not None:
            override = overrides.get((e.kind, (e.name or "").strip().lower()))
            results.append(
                _reuse_existing_entity(
                    cursor,
                    existing=existing,
                    extracted=e,
                    merged_description=override,
                )
            )
            continue
        cursor.execute(
            """
            INSERT INTO entities
                  (person_id, kind, name, description, aliases,
                   attributes, generation_prompt,
                   llm_provider, llm_model, prompt_version)
            VALUES (%s,        %s,   %s,   %s,          %s,
                    %s,         %s,
                    %s,           %s,        %s)
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
        new_id = cursor.fetchone()[0]
        results.append(
            EntityWriteResult(
                id=new_id,
                reused=False,
                description_changed=bool(e.description),
                embed_text=e.description or None,
            )
        )
    return results


def _find_existing_active_entity(
    cursor, *, person_id: str, kind: str, name: str
) -> tuple[str, str | None, list[str]] | None:
    """Oldest active same-kind entity whose normalized name matches ``name``.

    Returns ``(id, description, aliases)`` or ``None``. Oldest-first is the
    ambiguity guard: if pre-existing legacy duplicates share the name, we
    resolve to the oldest and let the reconcile backstop clean up the rest
    rather than guessing among them.
    """
    normalized = (name or "").strip().lower()
    if not normalized:
        return None
    cursor.execute(
        """
        SELECT id::text, description, COALESCE(aliases, '{}')
          FROM entities
         WHERE person_id = %s
           AND status = 'active'
           AND kind = %s
           AND lower(btrim(name)) = %s
         ORDER BY created_at ASC
         LIMIT 1
        """,
        (person_id, kind, normalized),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return row[0], row[1], list(row[2] or [])


def find_existing_active_entities_by_name(
    cursor, *, person_id: str, keys: list[tuple[str, str]]
) -> dict[tuple[str, str], tuple[str, str | None]]:
    """Batch lookup for the worker's pre-transaction description-merge step.

    ``keys`` is a list of ``(kind, name)``. Returns a map keyed by
    ``(kind, lower(name))`` → ``(entity_id, description)`` for the oldest
    active match per key. Empty input → empty result.
    """
    if not keys:
        return {}
    kinds = list({k for k, _ in keys})
    names = list({(n or "").strip().lower() for _, n in keys})
    names = [n for n in names if n]
    if not names:
        return {}
    cursor.execute(
        """
        SELECT DISTINCT ON (kind, lower(btrim(name)))
               kind, lower(btrim(name)) AS name_norm, id::text, description
          FROM entities
         WHERE person_id = %s
           AND status = 'active'
           AND kind = ANY(%s)
           AND lower(btrim(name)) = ANY(%s)
         ORDER BY kind, lower(btrim(name)), created_at ASC
        """,
        (person_id, kinds, names),
    )
    out: dict[tuple[str, str], tuple[str, str | None]] = {}
    for kind, name_norm, entity_id, description in cursor.fetchall():
        out[(kind, name_norm)] = (entity_id, description)
    return out


def _reuse_existing_entity(
    cursor,
    *,
    existing: tuple[str, str | None, list[str]],
    extracted: ExtractedEntity,
    merged_description: str | None = None,
) -> "EntityWriteResult":
    """Fold an extracted mention into an existing entity row.

    Adds any new aliases. For the description:
      * if ``merged_description`` is provided (the worker pre-computed an
        LLM blend of the existing + new descriptions), adopt it — this is
        how later mentions *enrich* an established entity blurb;
      * else fill the description only if the existing row has none (never
        clobber accumulated detail with a raw single mention).
    When the description changes, NULL the embedding columns and signal a
    re-embed.
    """
    existing_id, existing_desc, existing_aliases = existing

    merged_aliases = _fold_aliases(existing_aliases, extracted.aliases)
    aliases_changed = merged_aliases != list(existing_aliases or [])

    final_desc = existing_desc
    desc_changed = False
    blended = (merged_description or "").strip()
    if blended and blended != (existing_desc or "").strip():
        final_desc = blended
        desc_changed = True
    elif not (existing_desc or "").strip() and (extracted.description or "").strip():
        final_desc = extracted.description
        desc_changed = True

    if aliases_changed or desc_changed:
        if desc_changed:
            cursor.execute(
                """
                UPDATE entities
                   SET aliases = %s,
                       description = %s,
                       description_embedding = NULL,
                       embedding_model = NULL,
                       embedding_model_version = NULL
                 WHERE id = %s
                """,
                (merged_aliases, final_desc, existing_id),
            )
        else:
            cursor.execute(
                "UPDATE entities SET aliases = %s WHERE id = %s",
                (merged_aliases, existing_id),
            )

    log.info(
        "extraction.entity_reused",
        entity_id=existing_id,
        name=extracted.name,
        kind=extracted.kind,
        description_changed=desc_changed,
    )
    return EntityWriteResult(
        id=existing_id,
        reused=True,
        description_changed=desc_changed,
        embed_text=final_desc if desc_changed else None,
    )


def _fold_aliases(existing: list[str], additions: list[str]) -> list[str]:
    """Append new aliases (case-insensitive dedup), preserving order."""
    out = list(existing or [])
    seen = {a.strip().lower() for a in out if a and a.strip()}
    for raw in additions or []:
        alias = (raw or "").strip()
        if not alias or alias.lower() in seen:
            continue
        seen.add(alias.lower())
        out.append(alias)
    return out


def find_existing_active_traits_by_name(
    cursor,
    *,
    person_id: str,
    names: list[str],
) -> dict[str, ExistingTraitRow]:
    """Return active traits whose case-insensitive name is in ``names``.

    Keyed by ``lower(name)`` so callers can match without re-lowercasing.
    Empty input → empty result. Used by the worker (outside the
    transaction) to decide which extracted traits should merge into an
    existing row vs insert a new one. See invariant #18.
    """
    if not names:
        return {}
    lowered = list({n.strip().lower() for n in names if n and n.strip()})
    if not lowered:
        return {}
    cursor.execute(
        """
        SELECT id::text, name, description
          FROM active_traits
         WHERE person_id = %s
           AND lower(name) = ANY(%s)
        """,
        (person_id, lowered),
    )
    out: dict[str, ExistingTraitRow] = {}
    for row in cursor.fetchall():
        out[row[1].strip().lower()] = ExistingTraitRow(
            id=row[0], name=row[1], description=row[2]
        )
    return out


def _insert_traits(
    cursor,
    *,
    person_id: str,
    traits,
    llm_provenance: LLMProvenance | None,
    merge_resolutions: list["TraitMergeResolution | None"] | None = None,
) -> list[str]:
    """Insert new traits or UPDATE existing rows for cross-session merges.

    When ``merge_resolutions[i]`` is set, that trait already exists for
    this person; we UPDATE its description, NULL its embedding fields
    (the embedding worker re-embeds on the merged description), and
    return the existing id at position ``i``. When the resolution is
    ``None``, INSERT a fresh row as usual. The returned list has the
    same length and order as ``traits``.
    """
    if merge_resolutions is not None and len(merge_resolutions) != len(traits):
        raise ValueError(
            "merge_resolutions length must match traits length"
        )
    ids: list[str] = []
    for i, t in enumerate(traits):
        resolution = (
            merge_resolutions[i] if merge_resolutions is not None else None
        )
        if resolution is not None:
            cursor.execute(
                """
                UPDATE traits
                   SET description             = %s,
                       description_embedding   = NULL,
                       embedding_model         = NULL,
                       embedding_model_version = NULL
                 WHERE id = %s
                   AND person_id = %s
                   AND status = 'active'
                """,
                (
                    t.description,
                    resolution.existing_trait_id,
                    person_id,
                ),
            )
            ids.append(resolution.existing_trait_id)
            log.info(
                "extraction.trait_merged",
                trait_id=resolution.existing_trait_id,
                name=t.name,
            )
            continue
        cursor.execute(
            """
            INSERT INTO traits
                  (person_id, name, description, strength,
                   llm_provider, llm_model, prompt_version)
            VALUES (%s,        %s,   %s,          'mentioned_once',
                    %s,           %s,        %s)
            RETURNING id::text
            """,
            (
                person_id,
                t.name,
                t.description,
                llm_provenance.provider if llm_provenance else None,
                llm_provenance.model if llm_provenance else None,
                llm_provenance.prompt_version if llm_provenance else None,
            ),
        )
        ids.append(cursor.fetchone()[0])
    return ids


def _validate_collection_tags(raw: list[str]) -> list[str]:
    """Filter LLM-emitted collection slugs to the taggable grid registry.

    Unknown slugs are dropped silently (invariant #6); order preserved and
    deduped. Returns ``[]`` when nothing valid remains — never NULL, so a
    freshly-extracted moment is always distinguishable from a never-tagged
    (backfill-pending) one at the column level (design 2026-07-06)."""
    seen: set[str] = set()
    out: list[str] = []
    for slug in raw or ():
        s = (slug or "").strip()
        if s in _TAGGABLE_SLUG_SET and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _insert_moment(
    cursor,
    *,
    person_id: str,
    moment: ExtractedMoment,
    llm_provenance: LLMProvenance | None,
) -> str:
    time_anchor_payload: Any = None
    if moment.time_anchor is not None:
        ta = moment.time_anchor.model_dump(exclude_none=True)
        time_anchor_payload = Json(ta) if ta else None

    cursor.execute(
        """
        INSERT INTO moments
              (person_id, title, narrative, time_anchor,
               life_period_estimate, sensory_details, emotional_tone,
               contributor_perspective, generation_prompt,
               storybook_collections,
               llm_provider, llm_model, prompt_version)
        VALUES (%s,        %s,    %s,        %s,
                %s,                  %s,              %s,
                %s,                       %s,
                %s,
                %s,           %s,        %s)
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
            _validate_collection_tags(moment.collections),
            llm_provenance.provider if llm_provenance else None,
            llm_provenance.model if llm_provenance else None,
            llm_provenance.prompt_version if llm_provenance else None,
        ),
    )
    return cursor.fetchone()[0]


def _insert_dropped_reference_questions(
    cursor,
    *,
    person_id: str,
    dropped_references,
    llm_provenance: LLMProvenance | None,
) -> list[str]:
    ids: list[str] = []
    for dr in dropped_references:
        if _dropped_phrase_already_active(
            cursor, person_id=person_id, phrase=dr.dropped_phrase
        ):
            # An active dropped_reference question already covers this phrase.
            # Re-minting a fresh row would duplicate the bank and, worse,
            # escape any user suppress recorded against the existing row
            # (suppression is keyed on question_id). Skip silently —
            # invariant #6: under-extract rather than pile up.
            continue
        attrs = {
            "dropped_phrase": dr.dropped_phrase,
            "themes": list(dr.themes),
        }
        cursor.execute(
            """
            INSERT INTO questions
                  (person_id, text, source, attributes,
                   llm_provider, llm_model, prompt_version)
            VALUES (%s,        %s,   'dropped_reference', %s,
                    %s,           %s,        %s)
            RETURNING id::text
            """,
            (
                person_id,
                dr.question_text,
                Json(attrs),
                llm_provenance.provider if llm_provenance else None,
                llm_provenance.model if llm_provenance else None,
                llm_provenance.prompt_version if llm_provenance else None,
            ),
        )
        ids.append(cursor.fetchone()[0])
    return ids


def _dropped_phrase_already_active(cursor, *, person_id: str, phrase: str | None) -> bool:
    """True when an active dropped_reference question already covers ``phrase``.

    Match is case-insensitive and whitespace-trimmed, mirroring the
    selector's semantic-suppress generalisation in
    ``SELECT_STEADY_CANDIDATES`` so dedup and exclusion agree on what
    "the same phrase" means.
    """
    if not phrase or not phrase.strip():
        return False
    cursor.execute(
        """
        SELECT 1
          FROM active_questions q
         WHERE q.person_id = %(pid)s
           AND q.source    = 'dropped_reference'
           AND lower(btrim(q.attributes->>'dropped_phrase')) = lower(btrim(%(phrase)s))
         LIMIT 1
        """,
        {"pid": person_id, "phrase": phrase},
    )
    return cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def _insert_edge(
    cursor,
    *,
    from_kind: str,
    from_id: str,
    to_kind: str,
    to_id: str,
    edge_type: str,
    attributes: dict | None = None,
) -> None:
    """Single validated edge insert. ``ON CONFLICT DO NOTHING`` guards the
    UNIQUE (from, to, type) constraint added in 0001."""
    validate_edge(from_kind, to_kind, edge_type)
    cursor.execute(
        """
        INSERT INTO edges (from_kind, from_id, to_kind, to_id,
                           edge_type, attributes)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (from_kind, from_id, to_kind, to_id, edge_type)
        DO NOTHING
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


def _insert_moment_edges(
    cursor,
    *,
    moment_id: str,
    moment: ExtractedMoment,
    entity_index_to_id: dict[int, str | None],
    entity_kinds: list[str],
    trait_ids: list[str],
) -> None:
    for idx in moment.involves_entity_indexes:
        target_id = entity_index_to_id.get(idx)
        if target_id is None:
            continue
        _insert_edge(
            cursor,
            from_kind="moment",
            from_id=moment_id,
            to_kind="entity",
            to_id=target_id,
            edge_type="involves",
        )

    if moment.happened_at_entity_index is not None:
        idx = moment.happened_at_entity_index
        target_id = entity_index_to_id.get(idx)
        if target_id is not None:
            # Sub-kind requirement: happened_at must point at a place.
            if not (0 <= idx < len(entity_kinds)):
                pass
            elif entity_kinds[idx] != "place":
                log.warning(
                    "extraction.happened_at_not_place_dropped",
                    moment_id=moment_id,
                    target_kind=entity_kinds[idx],
                )
            else:
                _insert_edge(
                    cursor,
                    from_kind="moment",
                    from_id=moment_id,
                    to_kind="entity",
                    to_id=target_id,
                    edge_type="happened_at",
                )

    for idx in moment.exemplifies_trait_indexes:
        if not (0 <= idx < len(trait_ids)):
            continue
        _insert_edge(
            cursor,
            from_kind="moment",
            from_id=moment_id,
            to_kind="trait",
            to_id=trait_ids[idx],
            edge_type="exemplifies",
        )


def _insert_themed_as_edges(
    cursor,
    *,
    moment_id: str,
    theme_slugs: list[str],
    theme_slug_to_id: dict[str, str],
) -> None:
    """Write one ``themed_as`` edge per resolvable slug.

    Unknown slugs (not in the map) are dropped silently — they may be
    LLM hallucinations or slugs for a theme that was superseded between
    the LLM call and the transaction. Duplicates within the moment's
    own list collapse on the UNIQUE edge constraint.
    """
    if not theme_slugs:
        return
    seen: set[str] = set()
    for slug in theme_slugs:
        normalized = (slug or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        theme_id = theme_slug_to_id.get(normalized)
        if theme_id is None:
            log.info(
                "extraction.theme_slug_unknown",
                moment_id=moment_id,
                slug=slug,
            )
            continue
        _insert_edge(
            cursor,
            from_kind="moment",
            from_id=moment_id,
            to_kind="theme",
            to_id=theme_id,
            edge_type="themed_as",
        )


def _insert_entity_related_edges(
    cursor,
    *,
    surviving_entities: list[ExtractedEntity],
    entity_ids: list[str],
) -> None:
    """Emit related_to edges between entities. Self-references are blocked
    upstream by the schema validator; we still defend against them here."""
    seen: set[tuple[str, str]] = set()
    for src_idx, entity in enumerate(surviving_entities):
        for dst_idx in entity.related_to_entity_indexes:
            if not (0 <= dst_idx < len(surviving_entities)):
                continue
            if src_idx == dst_idx:
                continue
            from_id = entity_ids[src_idx]
            to_id = entity_ids[dst_idx]
            key = (from_id, to_id)
            if key in seen:
                continue
            seen.add(key)
            _insert_edge(
                cursor,
                from_kind="entity",
                from_id=from_id,
                to_kind="entity",
                to_id=to_id,
                edge_type="related_to",
            )


def _insert_answered_by_edges(
    cursor, *, question_id: str, moment_ids: list[str]
) -> None:
    for mid in moment_ids:
        _insert_edge(
            cursor,
            from_kind="question",
            from_id=question_id,
            to_kind="moment",
            to_id=mid,
            edge_type="answered_by",
        )


# ---------------------------------------------------------------------------
# Supersession (invariant #5)
# ---------------------------------------------------------------------------


def _supersede_moment(
    cursor, *, old_moment_id: str, new_moment_id: str
) -> None:
    cursor.execute(
        """
        UPDATE moments
           SET status = 'superseded',
               superseded_by = %s
         WHERE id = %s
           AND status = 'active'
        """,
        (new_moment_id, old_moment_id),
    )

    # Inbound edges (anything pointing AT the old moment): repoint to the
    # new id, but first delete any that would collide with edges already
    # pointing at the new moment (UNIQUE constraint on
    # (from_kind, from_id, to_kind, to_id, edge_type)).
    cursor.execute(
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
    cursor.execute(
        """
        UPDATE edges
           SET to_id = %s
         WHERE to_kind = 'moment'
           AND to_id   = %s
        """,
        (new_moment_id, old_moment_id),
    )

    # Outbound edges from the old moment are dropped; the new moment
    # gets fresh outbound edges from this extraction.
    cursor.execute(
        """
        DELETE FROM edges
         WHERE from_kind = 'moment'
           AND from_id   = %s
        """,
        (old_moment_id,),
    )


# ---------------------------------------------------------------------------
# Coverage signal
# ---------------------------------------------------------------------------


def _coverage_signal_for(
    *,
    moment: ExtractedMoment,
    entity_index_to_id: dict[int, str | None],
    surviving_entities: list[ExtractedEntity],
    has_traits_in_segment: bool,
) -> MomentCoverageSignal:
    """
    Compute the booleans the Coverage Tracker increments per dimension.

    Per CLAUDE.md §6 / ARCHITECTURE.md §3.10:
      * sensory  — sensory_details non-empty
      * voice    — a trait was extracted in this segment, OR a linked
                   person entity has a saying/mannerism attribute
      * place    — any involves edge to a place entity, OR a happened_at
                   edge (which points to a place)
      * relation — any involves edge to a person entity (≠ subject; the
                   subject guard already removed self-references)
      * era      — time_anchor.year is set, OR life_period_estimate set
    """
    has_sensory = bool(moment.sensory_details)

    referenced_entities: list[ExtractedEntity] = []
    for idx in moment.involves_entity_indexes:
        if 0 <= idx < len(surviving_entities) and entity_index_to_id.get(idx):
            referenced_entities.append(surviving_entities[idx])
    if (
        moment.happened_at_entity_index is not None
        and 0 <= moment.happened_at_entity_index < len(surviving_entities)
        and entity_index_to_id.get(moment.happened_at_entity_index)
    ):
        referenced_entities.append(
            surviving_entities[moment.happened_at_entity_index]
        )

    has_place = any(e.kind == "place" for e in referenced_entities)
    has_non_subject_person = any(
        e.kind == "person" for e in referenced_entities
    )

    person_voice_signal = any(
        e.kind == "person"
        and (
            (e.attributes or {}).get("saying")
            or (e.attributes or {}).get("mannerism")
        )
        for e in referenced_entities
    )
    has_voice = has_traits_in_segment or person_voice_signal

    year_set = (
        moment.time_anchor is not None
        and moment.time_anchor.year is not None
    )
    has_era = bool(year_set or moment.life_period_estimate)

    return MomentCoverageSignal(
        has_sensory=has_sensory,
        has_voice=has_voice,
        has_place=has_place,
        has_non_subject_person=has_non_subject_person,
        has_era=has_era,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fetch_person(cursor, person_id: str) -> PersonRow:
    """Look up the legacy subject for the subject guard."""
    cursor.execute(
        """
        SELECT id::text, name, COALESCE(NULL::text[], ARRAY[]::text[]),
               gender, relationship, contributor_gender
          FROM persons
         WHERE id = %s
        """,
        (person_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"person {person_id!r} not found")
    pid, name, _aliases, gender, relationship, contributor_gender = row
    # ``persons`` does not currently carry an aliases column; we expose
    # an empty list and let future schema additions plug into the same
    # entry point without churning the call sites.
    return PersonRow(
        id=pid,
        name=name,
        aliases=[],
        gender=gender,
        relationship=relationship,
        contributor_gender=contributor_gender,
    )


def fetch_active_entities_for_catalog(
    cursor, *, person_id: str, limit: int = 200
) -> list[tuple[str, str, list[str], str]]:
    """Active entities for the extraction prompt's ``<entity_catalog>``.

    Returns ``(name, kind, aliases, description)`` rows across ALL kinds
    (prevention layer 2). Most-recent first, capped — the catalog is a
    reuse hint, not an exhaustive dump.
    """
    cursor.execute(
        """
        SELECT name, kind, COALESCE(aliases, '{}'),
               COALESCE(left(description, 160), '')
          FROM entities
         WHERE person_id = %s
           AND status = 'active'
         ORDER BY updated_at DESC
         LIMIT %s
        """,
        (person_id, limit),
    )
    return [
        (row[0], row[1], list(row[2] or []), row[3] or "")
        for row in cursor.fetchall()
    ]


def iter_inserted_moments(result: PersistenceResult) -> Iterable[tuple[str, str]]:
    """Yield (moment_id, generation_prompt-source) pairs."""
    return zip(result.moment_ids, result.moment_ids, strict=True)
