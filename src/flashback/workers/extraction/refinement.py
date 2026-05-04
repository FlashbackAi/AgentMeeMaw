"""
Refinement candidate search.

Per ARCHITECTURE.md §8(A) the algorithm is two-stage:

  1. Vector search over ``active_moments`` for the same person, using the
     new moment's narrative embedded **as a query**. Cosine distance must
     be below a tunable threshold (default 0.35).
  2. Entity-overlap filter — at least one entity name in common between
     the new moment (resolved from its ``involves_entity_indexes``) and
     the candidate (joined via ``edges``).

The compatibility LLM only fires once per candidate that survives both
stages. Most segments produce zero candidates and zero LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from .schema import ExtractedMoment, ExtractionResult
from .voyage_query import SyncVoyageQueryEmbedder

log = structlog.get_logger("flashback.workers.extraction.refinement")


@dataclass(frozen=True)
class RefinementCandidate:
    """One existing moment that could be refined by the new one."""

    id: str
    title: str
    narrative: str
    distance: float


def find_refinement_candidates(
    *,
    new_moment: ExtractedMoment,
    new_moment_entity_names: list[str],
    person_id: str,
    voyage: SyncVoyageQueryEmbedder,
    db_pool,
    embedding_model: str,
    embedding_model_version: str,
    distance_threshold: float = 0.35,
    candidate_limit: int = 3,
) -> list[RefinementCandidate]:
    """
    Return zero or more refinement candidates for ``new_moment``.

    The vector query is scoped tightly: ``person_id`` (invariant #2),
    ``status='active'`` via the ``active_moments`` view (#1), and matching
    embedding model identity (#3). Voyage failure is treated as "no
    candidates" — refinement detection is best-effort.
    """
    query_vector = voyage.embed(new_moment.narrative)
    if query_vector is None:
        return []

    sql = """
        SELECT id::text, title, narrative,
               (narrative_embedding <=> %(qv)s::vector) AS distance
        FROM   active_moments
        WHERE  person_id              = %(person_id)s
          AND  embedding_model         = %(model)s
          AND  embedding_model_version = %(version)s
          AND  narrative_embedding IS NOT NULL
          AND  (narrative_embedding <=> %(qv)s::vector) < %(thr)s
        ORDER BY narrative_embedding <=> %(qv)s::vector
        LIMIT  %(limit)s
    """
    params = {
        "qv": query_vector,
        "person_id": person_id,
        "model": embedding_model,
        "version": embedding_model_version,
        "thr": distance_threshold,
        "limit": candidate_limit,
    }
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    if not rows:
        return []

    new_names = {n.lower() for n in new_moment_entity_names if n}
    if not new_names:
        # No entities on the new moment means the entity-overlap filter
        # admits nothing. Return early.
        return []

    candidates: list[RefinementCandidate] = []
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                moment_id, title, narrative, distance = row
                cur.execute(
                    """
                    SELECT lower(e.name)
                      FROM active_edges ed
                      JOIN active_entities e
                        ON e.id = ed.to_id
                     WHERE ed.from_kind = 'moment'
                       AND ed.from_id  = %s
                       AND ed.to_kind  = 'entity'
                       AND ed.edge_type IN ('involves', 'happened_at')
                    """,
                    (moment_id,),
                )
                existing_names = {r[0] for r in cur.fetchall()}
                if new_names & existing_names:
                    candidates.append(
                        RefinementCandidate(
                            id=moment_id,
                            title=title,
                            narrative=narrative,
                            distance=float(distance),
                        )
                    )

    log.info(
        "refinement.candidates",
        person_id=person_id,
        vector_hits=len(rows),
        kept=len(candidates),
    )
    return candidates


def collect_entity_names_for_moment(
    extraction: ExtractionResult, moment: ExtractedMoment
) -> list[str]:
    """Resolve a moment's entity-index references back to plain names."""
    names: list[str] = []
    for i in moment.involves_entity_indexes:
        if 0 <= i < len(extraction.entities):
            names.append(extraction.entities[i].name)
    if moment.happened_at_entity_index is not None and 0 <= moment.happened_at_entity_index < len(
        extraction.entities
    ):
        names.append(extraction.entities[moment.happened_at_entity_index].name)
    return names
