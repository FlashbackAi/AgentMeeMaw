"""Background-style scanner for user-reviewed identity merge suggestions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from .schema import IdentityMergeScanResponse
from .verifier import IdentityMergeVerification

log = structlog.get_logger("flashback.identity_merges.scanner")

VerifierFn = Callable[["IdentityMergeCandidate"], Awaitable[IdentityMergeVerification]]


@dataclass(frozen=True)
class IdentityMergeCandidate:
    person_id: str
    source_id: str
    source_name: str
    source_description: str
    source_aliases: list[str]
    target_id: str
    target_name: str
    target_description: str
    target_aliases: list[str]
    kind: str
    proposed_alias: str
    reason_kind: str
    embedding_distance: float | None


async def scan_identity_merge_suggestions_async(
    cursor,
    *,
    person_id: str,
    verifier: VerifierFn,
    embedding_distance_threshold: float = 0.18,
    limit: int = 20,
) -> IdentityMergeScanResponse:
    """
    Find likely duplicate identity rows, verify with a small LLM, and write
    pending suggestions. The graph is not mutated here.

    Embedding distance is used as supporting rank/context, not as a
    standalone reason to propose a merge. In this domain, related people and
    places often have very similar descriptions but are still separate
    identities.
    """

    candidates = await _find_candidates(
        cursor,
        person_id=person_id,
        limit=limit,
    )
    suggestion_ids: list[str] = []
    verifier_calls = 0

    for candidate in candidates:
        verifier_calls += 1
        verification = await verifier(candidate)
        if verification.verdict != "same_identity":
            log.info(
                "identity_merge.candidate_rejected_by_verifier",
                person_id=person_id,
                source_entity_id=candidate.source_id,
                target_entity_id=candidate.target_id,
                verdict=verification.verdict,
                confidence=verification.confidence,
            )
            continue
        inserted_id = await _insert_scanner_suggestion(
            cursor,
            candidate=candidate,
            verifier_reason=verification.reasoning,
        )
        if inserted_id:
            suggestion_ids.append(inserted_id)

    if suggestion_ids:
        log.info(
            "identity_merge.scanner_suggestions_created",
            person_id=person_id,
            count=len(suggestion_ids),
        )
    return IdentityMergeScanResponse(
        person_id=person_id,
        candidates_considered=len(candidates),
        verifier_calls=verifier_calls,
        suggestions_created=len(suggestion_ids),
        suggestion_ids=suggestion_ids,
    )


async def _find_candidates(
    cursor,
    *,
    person_id: str,
    limit: int,
) -> list[IdentityMergeCandidate]:
    await cursor.execute(
        """
        SELECT a.id::text, a.name, COALESCE(a.description, ''), COALESCE(a.aliases, '{}'),
               b.id::text, b.name, COALESCE(b.description, ''), COALESCE(b.aliases, '{}'),
               a.kind,
               CASE
                 WHEN a.description_embedding IS NOT NULL
                  AND b.description_embedding IS NOT NULL
                 THEN a.description_embedding <=> b.description_embedding
                 ELSE NULL
               END AS embedding_distance
          FROM entities a
          JOIN entities b
            ON b.person_id = a.person_id
           AND b.status = 'active'
           AND b.kind = a.kind
           AND b.id > a.id
         WHERE a.person_id = %s
           AND a.status = 'active'
           AND NOT EXISTS (
                 SELECT 1
                   FROM identity_merge_suggestions s
                  WHERE s.person_id = a.person_id
                    AND (
                         (s.source_entity_id = a.id AND s.target_entity_id = b.id)
                      OR (s.source_entity_id = b.id AND s.target_entity_id = a.id)
                    )
               )
           AND (
                lower(a.name) = lower(b.name)
             OR position(lower(a.name) in lower(COALESCE(b.description, ''))) > 0
             OR position(lower(b.name) in lower(COALESCE(a.description, ''))) > 0
             OR EXISTS (
                   SELECT 1
                     FROM unnest(COALESCE(a.aliases, '{}')) AS alias
                    WHERE lower(alias) = lower(b.name)
                )
             OR EXISTS (
                   SELECT 1
                     FROM unnest(COALESCE(b.aliases, '{}')) AS alias
                    WHERE lower(alias) = lower(a.name)
                )
           )
         ORDER BY
               CASE
                 WHEN lower(a.name) = lower(b.name) THEN 0
                 WHEN EXISTS (
                        SELECT 1 FROM unnest(COALESCE(a.aliases, '{}')) AS alias
                         WHERE lower(alias) = lower(b.name)
                      ) THEN 1
                 WHEN EXISTS (
                        SELECT 1 FROM unnest(COALESCE(b.aliases, '{}')) AS alias
                         WHERE lower(alias) = lower(a.name)
                      ) THEN 1
                 ELSE 2
               END,
               embedding_distance ASC NULLS LAST,
               GREATEST(length(COALESCE(a.description, '')), length(COALESCE(b.description, ''))) DESC
         LIMIT %s
        """,
        (person_id, limit),
    )
    rows = await cursor.fetchall()
    return [_orient_candidate(person_id, row) for row in rows]


def _orient_candidate(person_id: str, row: tuple[Any, ...]) -> IdentityMergeCandidate:
    (
        a_id,
        a_name,
        a_description,
        a_aliases,
        b_id,
        b_name,
        b_description,
        b_aliases,
        kind,
        embedding_distance,
    ) = row
    a_aliases = list(a_aliases or [])
    b_aliases = list(b_aliases or [])

    if _label_points_to_target(a_name, target_description=b_description, target_aliases=b_aliases):
        source = (a_id, a_name, a_description, a_aliases)
        target = (b_id, b_name, b_description, b_aliases)
        reason_kind = "alias_or_description"
    elif _label_points_to_target(b_name, target_description=a_description, target_aliases=a_aliases):
        source = (b_id, b_name, b_description, b_aliases)
        target = (a_id, a_name, a_description, a_aliases)
        reason_kind = "alias_or_description"
    elif _norm(a_name) == _norm(b_name):
        source, target = _source_target_by_detail(
            (a_id, a_name, a_description, a_aliases),
            (b_id, b_name, b_description, b_aliases),
        )
        reason_kind = "same_name"
    else:
        source, target = _source_target_by_detail(
            (a_id, a_name, a_description, a_aliases),
            (b_id, b_name, b_description, b_aliases),
        )
        reason_kind = "embedding_similarity"

    source_id, source_name, source_description, source_aliases = source
    target_id, target_name, target_description, target_aliases = target
    return IdentityMergeCandidate(
        person_id=person_id,
        source_id=source_id,
        source_name=source_name,
        source_description=source_description,
        source_aliases=source_aliases,
        target_id=target_id,
        target_name=target_name,
        target_description=target_description,
        target_aliases=target_aliases,
        kind=kind,
        proposed_alias=source_name,
        reason_kind=reason_kind,
        embedding_distance=float(embedding_distance) if embedding_distance is not None else None,
    )


async def _insert_scanner_suggestion(
    cursor,
    *,
    candidate: IdentityMergeCandidate,
    verifier_reason: str,
) -> str | None:
    await cursor.execute(
        """
        INSERT INTO identity_merge_suggestions
              (person_id, source_entity_id, target_entity_id,
               proposed_alias, reason, source)
        SELECT %s, %s, %s, %s, %s, 'scanner'
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
            candidate.person_id,
            candidate.source_id,
            candidate.target_id,
            candidate.proposed_alias,
            _reason(candidate, verifier_reason),
            candidate.person_id,
            candidate.source_id,
            candidate.target_id,
            candidate.target_id,
            candidate.source_id,
        ),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


def _label_points_to_target(
    label: str,
    *,
    target_description: str,
    target_aliases: list[str],
) -> bool:
    label_norm = _norm(label)
    if not label_norm:
        return False
    if label_norm in {_norm(alias) for alias in target_aliases}:
        return True
    return label_norm in _norm(target_description)


def _source_target_by_detail(left, right):
    left_score = len(left[2] or "") + (len(left[3] or []) * 20)
    right_score = len(right[2] or "") + (len(right[3] or []) * 20)
    if right_score >= left_score:
        return left, right
    return right, left


def _reason(candidate: IdentityMergeCandidate, verifier_reason: str) -> str:
    basis = {
        "same_name": "The entities have the same normalized name.",
        "alias_or_description": "One entity label appears as an alias or description detail for the other.",
        "embedding_similarity": "The entity descriptions are close in embedding space.",
    }.get(candidate.reason_kind, "The entities matched identity-merge heuristics.")
    return f"{basis} Verifier: {verifier_reason}"


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()
