"""
Refinement candidate search.

Per ARCHITECTURE.md §8(A) the algorithm is two-stage:

  1. Similarity over ``active_moments`` for the same person, using the
     new moment's narrative embedded **as a query**. Cosine distance must
     be below a tunable threshold (default 0.35). Moments the embedding
     worker hasn't caught up with yet (rows with a NULL embedding, written
     in the last hour) are compared query-side with the same Voyage
     embedder — retells cluster within minutes, exactly the window where
     the stored embedding doesn't exist yet, so skipping them minted
     duplicates (prod-test-5, 2026-07-29: twin extracted 36s after the
     original, whose embedding landed 12 minutes later).
  2. Entity-overlap filter — a shared entity name confirms a candidate.
     Missing evidence is NOT negative evidence: when either side carries
     no entity links at all, the candidate is still admitted under a
     stricter distance floor (default 0.20) and the compatibility LLM
     decides. Only a genuine disagreement — both sides linked, zero
     overlap — drops the candidate (two different weddings can share a
     narrative shape). The old hard requirement dropped 0.10-distance
     twins whenever the retell was extracted without entity links
     (Swetha, 2026-07-29).

The compatibility LLM only fires once per candidate that survives both
stages. Most segments produce zero candidates and zero LLM calls.
"""

from __future__ import annotations

import math
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
    no_entity_distance_threshold: float = 0.20,
    unembedded_lookback_minutes: int = 60,
    unembedded_candidate_limit: int = 3,
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
        # Voyage outage/failure — NOT "no similar moments". Refinement stays
        # best-effort (the segment still persists), but while this fires,
        # supersession is off and every segment mints fresh duplicates, so
        # the degradation must be distinguishable/alertable in logs.
        log.error(
            "refinement.skipped_voyage_unavailable",
            person_id=person_id,
        )
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
            rows = list(cur.fetchall())

    rows.extend(
        _recent_unembedded_hits(
            person_id=person_id,
            query_vector=query_vector,
            voyage=voyage,
            db_pool=db_pool,
            distance_threshold=distance_threshold,
            lookback_minutes=unembedded_lookback_minutes,
            limit=unembedded_candidate_limit,
        )
    )

    if not rows:
        return []
    rows.sort(key=lambda r: r[3])
    rows = rows[:candidate_limit]

    new_names = {n.lower() for n in new_moment_entity_names if n}

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
                if new_names and existing_names:
                    # Both sides carry entity evidence: require agreement.
                    admitted = bool(new_names & existing_names)
                else:
                    # Evidence missing on at least one side: absence is not
                    # disagreement — admit near-twins and let the compat
                    # LLM decide.
                    admitted = float(distance) < no_entity_distance_threshold
                if admitted:
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


def _recent_unembedded_hits(
    *,
    person_id: str,
    query_vector: list[float],
    voyage: SyncVoyageQueryEmbedder,
    db_pool,
    distance_threshold: float,
    lookback_minutes: int,
    limit: int,
) -> list[tuple[str, str, str, float]]:
    """Distance-scored rows for recent moments with no stored embedding.

    Their narratives are embedded query-side (nothing is written back —
    the embedding worker still owns the stored vector, invariant #4).
    Per-narrative Voyage failure just skips that candidate.
    """
    if limit <= 0:
        return []
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, title, narrative
                  FROM active_moments
                 WHERE person_id = %(person_id)s
                   AND narrative_embedding IS NULL
                   AND created_at > now() - make_interval(mins => %(lookback)s)
                 ORDER BY created_at DESC
                 LIMIT %(limit)s
                """,
                {
                    "person_id": person_id,
                    "lookback": lookback_minutes,
                    "limit": limit,
                },
            )
            recent = cur.fetchall()

    hits: list[tuple[str, str, str, float]] = []
    for moment_id, title, narrative in recent:
        candidate_vector = voyage.embed(narrative)
        if candidate_vector is None:
            continue
        distance = _cosine_distance(query_vector, candidate_vector)
        if distance is not None and distance < distance_threshold:
            hits.append((moment_id, title, narrative, distance))
    if recent:
        log.info(
            "refinement.unembedded_candidates",
            person_id=person_id,
            considered=len(recent),
            within_threshold=len(hits),
        )
    return hits


def _cosine_distance(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or not a:
        return None
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    return 1.0 - (dot / (norm_a * norm_b))


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
