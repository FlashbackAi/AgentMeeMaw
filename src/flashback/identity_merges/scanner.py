"""Background-style scanner for user-reviewed identity merge suggestions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from .disposition import decide_disposition
from .repository import auto_merge_async
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
    push_embedding=None,
    embedding_model: str = "",
    embedding_model_version: str = "",
) -> IdentityMergeScanResponse:
    """
    Find likely duplicate identity rows (name/alias evidence only, same
    kind), verify each with a small LLM, and route by disposition:

      * ``auto_merge`` (same_identity + high) — merge silently + notify;
      * ``ask`` (same_identity + medium, or unsure) — write a pending row;
      * ``drop`` — write nothing.

    Candidates are gated on name/alias evidence only — co-occurrence in a
    description never triggers, and cross-kind pairs never match.
    Embedding distance is passed to the verifier as context, never as a
    trigger: related people/places often have very similar descriptions
    but are still separate identities.

    The auto-merge path requires ``push_embedding`` + embedding model
    coordinates to re-embed the survivor; if ``push_embedding`` is None the
    merge still applies and the re-embed is skipped.
    """

    candidates = await _find_candidates(
        cursor,
        person_id=person_id,
        limit=limit,
    )
    suggestion_ids: list[str] = []
    verifier_calls = 0
    auto_merged_count = 0

    for candidate in candidates:
        verifier_calls += 1
        verification = await verifier(candidate)
        disposition = decide_disposition(
            verification.verdict, verification.confidence
        )
        if disposition == "drop":
            log.info(
                "identity_merge.candidate_dropped",
                person_id=person_id,
                source_entity_id=candidate.source_id,
                target_entity_id=candidate.target_id,
                verdict=verification.verdict,
                confidence=verification.confidence,
            )
            continue

        if disposition == "auto_merge":
            merged_id = await auto_merge_async(
                cursor,
                person_id=person_id,
                source_id=candidate.source_id,
                target_id=candidate.target_id,
                proposed_alias=candidate.proposed_alias,
                confidence=verification.confidence,
                notification_text=_notification_text(candidate, verification.reasoning),
                push_embedding=push_embedding,
                embedding_model=embedding_model,
                embedding_model_version=embedding_model_version,
            )
            if merged_id:
                suggestion_ids.append(merged_id)
                auto_merged_count += 1
                log.info(
                    "identity_merge.auto_merged",
                    person_id=person_id,
                    source_entity_id=candidate.source_id,
                    target_entity_id=candidate.target_id,
                )
            continue

        # disposition == "ask"
        inserted_id = await _insert_scanner_suggestion(
            cursor,
            candidate=candidate,
            verifier_reason=verification.reasoning,
            confidence=verification.confidence,
        )
        if inserted_id:
            suggestion_ids.append(inserted_id)

    if suggestion_ids:
        log.info(
            "identity_merge.scanner_suggestions_created",
            person_id=person_id,
            count=len(suggestion_ids),
            auto_merged=auto_merged_count,
        )
    return IdentityMergeScanResponse(
        person_id=person_id,
        candidates_considered=len(candidates),
        verifier_calls=verifier_calls,
        suggestions_created=len(suggestion_ids) - auto_merged_count,
        auto_merged_count=auto_merged_count,
        suggestion_ids=suggestion_ids,
    )


def _notification_text(candidate: "IdentityMergeCandidate", verifier_reason: str) -> str:
    """User-facing toast text for an auto-merge. Prefers the LLM sentence."""
    reason = (verifier_reason or "").strip()
    if reason:
        return reason
    return (
        f"We combined two entries that both looked like "
        f"{candidate.target_name!r}. You can undo this if they're different."
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
                -- NAME / ALIAS EVIDENCE ONLY. Co-occurrence of one name in
                -- the other's description is NOT identity evidence and is
                -- deliberately excluded (it produced false positives like
                -- "Mokshith" vs "Mokshith's mother"). Embedding distance is
                -- computed below as verifier context but never triggers a
                -- candidate. Same-kind is already enforced by the JOIN.
                lower(a.name) = lower(b.name)
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

    if _norm(a_name) == _norm(b_name):
        source, target = _source_target_by_detail(
            (a_id, a_name, a_description, a_aliases),
            (b_id, b_name, b_description, b_aliases),
        )
        reason_kind = "same_name"
    elif _name_in_aliases(a_name, b_aliases):
        # a's name is recorded as an alias of b -> a is the older label, b canonical
        source = (a_id, a_name, a_description, a_aliases)
        target = (b_id, b_name, b_description, b_aliases)
        reason_kind = "alias"
    elif _name_in_aliases(b_name, a_aliases):
        source = (b_id, b_name, b_description, b_aliases)
        target = (a_id, a_name, a_description, a_aliases)
        reason_kind = "alias"
    else:
        # Should not happen — the candidate gate only matches on name or
        # alias evidence. Fall back to the by-detail orientation.
        source, target = _source_target_by_detail(
            (a_id, a_name, a_description, a_aliases),
            (b_id, b_name, b_description, b_aliases),
        )
        reason_kind = "same_name"

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
    confidence: str | None = None,
) -> str | None:
    await cursor.execute(
        """
        INSERT INTO identity_merge_suggestions
              (person_id, source_entity_id, target_entity_id,
               proposed_alias, reason, source, confidence)
        SELECT %s, %s, %s, %s, %s, 'scanner', %s
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
            confidence,
            candidate.person_id,
            candidate.source_id,
            candidate.target_id,
            candidate.target_id,
            candidate.source_id,
        ),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


def _name_in_aliases(label: str, aliases: list[str]) -> bool:
    """True when ``label`` matches one of ``aliases`` (case-insensitive).

    Note: we intentionally do NOT check descriptions. A name appearing in
    the other row's free-text description is co-occurrence, not identity
    evidence, and was the source of false-positive suggestions.
    """
    label_norm = _norm(label)
    if not label_norm:
        return False
    return label_norm in {_norm(alias) for alias in aliases}


def _source_target_by_detail(left, right):
    left_score = len(left[2] or "") + (len(left[3] or []) * 20)
    right_score = len(right[2] or "") + (len(right[3] or []) * 20)
    if right_score >= left_score:
        return left, right
    return right, left


def _reason(candidate: IdentityMergeCandidate, verifier_reason: str) -> str:
    # The verifier's reasoning is an LLM-authored, user-facing sentence and
    # is strongly preferred. The template strings below are only a fallback
    # for when the verifier returned no reasoning text.
    reason = verifier_reason.strip()
    if reason:
        return reason
    return {
        "same_name": f"Both rows are named {candidate.target_name!r}.",
        "alias": (
            f"{candidate.proposed_alias!r} is recorded as another name for "
            f"{candidate.target_name!r}."
        ),
    }.get(candidate.reason_kind, "These rows look like the same thing.")


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()
