"""Per-person persistence for the Trait Synthesizer.

The unit of work is one person. Inside a single transaction:

  1. Apply existing-trait decisions: for ``upgrade``/``downgrade``,
     advance one rung along the strength ladder and write
     ``thread → trait`` ``evidences`` edges from the supporting
     threads. ``keep`` is a no-op. Trait already at the ladder
     extreme is a no-op + log entry.
  2. Insert new traits proposed by the LLM. Skip any whose name
     matches an existing active trait for the person (defensive
     dedup; the LLM is told to prefer ``upgrade``).
  3. Write ``thread → trait`` ``evidences`` edges from each new
     trait's supporting threads.
  4. Insert the idempotency row.

If anything raises mid-way, the surrounding transaction rolls back
and the SQS message is not acked — SQS visibility timeout will
redrive.

Embedding pushes happen POST-COMMIT (in the worker), only for newly
inserted traits. Existing traits whose strength changed do NOT get
re-embedded — the embedded text (name + description) hasn't moved.

Invariants honoured (CLAUDE.md §4):

* #1 (status='active'): all reads scope to active rows; the dedup
  guard checks ``status='active'`` traits.
* #2 (person_id scoping): every write carries ``person_id``;
  cross-legacy bleed is impossible.
* #3 (no cross-model vectors): newly inserted traits leave the
  embedding columns NULL; the embedding worker stamps them with the
  configured (model, version) when it drains the embedding job.
* #4 (no inline embeddings): vectors are never written here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from psycopg.types.json import Json

from flashback.db.edges import validate_edge

from .idempotency import mark_processed
from .schema import (
    STRENGTH_LADDER,
    Strength,
    TraitSynthesisResult,
)

log = structlog.get_logger("flashback.workers.trait_synthesizer.persistence")


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class NewTraitRow:
    """One newly-inserted trait, surfaced for the post-commit embedding push."""

    id: str
    name: str
    description: str | None


@dataclass
class PersistResult:
    """What the worker needs after a successful commit."""

    upgraded_ids: list[str] = field(default_factory=list)
    downgraded_ids: list[str] = field(default_factory=list)
    new_traits: list[NewTraitRow] = field(default_factory=list)
    skipped_at_ladder_extreme: list[str] = field(default_factory=list)
    skipped_duplicate_names: list[str] = field(default_factory=list)
    new_evidence_edge_count: int = 0

    @property
    def created_count(self) -> int:
        return len(self.new_traits)

    @property
    def upgraded_count(self) -> int:
        return len(self.upgraded_ids)

    @property
    def downgraded_count(self) -> int:
        return len(self.downgraded_ids)

    def summary(self) -> dict:
        """Compact dict used in worker log lines."""
        return {
            "created": self.created_count,
            "upgraded": self.upgraded_count,
            "downgraded": self.downgraded_count,
            "ladder_extreme_skips": len(self.skipped_at_ladder_extreme),
            "duplicate_name_skips": len(self.skipped_duplicate_names),
            "new_evidence_edges": self.new_evidence_edge_count,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def persist_synthesis(
    cursor,
    *,
    person_id: str,
    result: TraitSynthesisResult,
    idempotency_key: str,
) -> PersistResult:
    """Run the full transactional write. Caller owns BEGIN/COMMIT/ROLLBACK."""
    out = PersistResult()

    # 1. Existing trait decisions.
    for decision in result.existing_trait_decisions:
        if decision.action == "keep":
            continue

        trait_id = str(decision.trait_id)
        current = _fetch_strength(cursor, trait_id=trait_id, person_id=person_id)
        if current is None:
            # Trait doesn't exist (or doesn't belong to this person, or is
            # inactive). Skip — model hallucinated an id.
            log.warning(
                "trait_synthesizer.unknown_trait_id",
                trait_id=trait_id,
                action=decision.action,
            )
            continue

        new_strength = _ladder_step(
            current_strength=current,
            direction="up" if decision.action == "upgrade" else "down",
        )
        if new_strength is None:
            log.info(
                "trait_synthesizer.trait_at_ladder_extreme",
                trait_id=trait_id,
                action=decision.action,
                current_strength=current,
            )
            out.skipped_at_ladder_extreme.append(trait_id)
            continue

        _update_strength(cursor, trait_id=trait_id, new_strength=new_strength)

        # Evidence edges from each supporting thread.
        for tid in decision.supporting_thread_ids:
            inserted = _insert_thread_to_trait_evidence(
                cursor,
                thread_id=str(tid),
                trait_id=trait_id,
            )
            if inserted:
                out.new_evidence_edge_count += 1

        if decision.action == "upgrade":
            out.upgraded_ids.append(trait_id)
        else:
            out.downgraded_ids.append(trait_id)

    # 2. New traits.
    for proposal in result.new_trait_proposals:
        if _trait_name_exists(cursor, person_id=person_id, name=proposal.name):
            log.info(
                "trait_synthesizer.skipping_duplicate_trait_name",
                name=proposal.name,
            )
            out.skipped_duplicate_names.append(proposal.name)
            continue

        new_id = _insert_trait(
            cursor,
            person_id=person_id,
            name=proposal.name,
            description=proposal.description,
            strength=proposal.initial_strength,
        )
        out.new_traits.append(
            NewTraitRow(
                id=new_id,
                name=proposal.name,
                description=proposal.description,
            )
        )

        # 3. Evidence edges for the new trait.
        for tid in proposal.supporting_thread_ids:
            inserted = _insert_thread_to_trait_evidence(
                cursor,
                thread_id=str(tid),
                trait_id=new_id,
            )
            if inserted:
                out.new_evidence_edge_count += 1

    # 4. Idempotency row — inside this transaction.
    mark_processed(
        cursor,
        idempotency_key=idempotency_key,
        person_id=person_id,
        traits_created=out.created_count,
        traits_upgraded=out.upgraded_count,
        traits_downgraded=out.downgraded_count,
    )

    return out


# ---------------------------------------------------------------------------
# Strength ladder
# ---------------------------------------------------------------------------


def _ladder_step(current_strength: Strength, direction: str) -> Strength | None:
    """Advance one rung. Returns None if at the boundary."""
    idx = STRENGTH_LADDER.index(current_strength)
    if direction == "up":
        return STRENGTH_LADDER[idx + 1] if idx + 1 < len(STRENGTH_LADDER) else None
    if direction == "down":
        return STRENGTH_LADDER[idx - 1] if idx - 1 >= 0 else None
    raise ValueError(f"unknown direction: {direction}")


# ---------------------------------------------------------------------------
# DB primitives
# ---------------------------------------------------------------------------


def _fetch_strength(cursor, *, trait_id: str, person_id: str) -> Strength | None:
    """Return the active trait's current strength, or None if not found.

    Filtering on ``person_id`` defends against a hallucinated id from
    another legacy.
    """
    cursor.execute(
        """
        SELECT strength
          FROM active_traits
         WHERE id = %s
           AND person_id = %s
        """,
        (trait_id, person_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return row[0]  # type: ignore[no-any-return]


def _update_strength(cursor, *, trait_id: str, new_strength: Strength) -> None:
    cursor.execute(
        """
        UPDATE traits
           SET strength = %s
         WHERE id = %s
        """,
        (new_strength, trait_id),
    )


def _trait_name_exists(cursor, *, person_id: str, name: str) -> bool:
    """Defensive dedup: case-insensitive match against active traits."""
    cursor.execute(
        """
        SELECT 1
          FROM active_traits
         WHERE person_id = %s
           AND lower(name) = lower(%s)
         LIMIT 1
        """,
        (person_id, name),
    )
    return cursor.fetchone() is not None


def _insert_trait(
    cursor,
    *,
    person_id: str,
    name: str,
    description: str,
    strength: Strength,
) -> str:
    """Insert a new active trait. Embedding columns are left NULL — the
    post-commit embedding push fills them in.
    """
    cursor.execute(
        """
        INSERT INTO traits (person_id, name, description, strength)
        VALUES             (%s,        %s,   %s,          %s)
        RETURNING id::text
        """,
        (person_id, name, description, strength),
    )
    return cursor.fetchone()[0]


def _insert_thread_to_trait_evidence(
    cursor,
    *,
    thread_id: str,
    trait_id: str,
) -> bool:
    """Insert one ``thread → trait`` ``evidences`` edge.

    ON CONFLICT DO NOTHING on the unique edge tuple — running the
    synthesizer twice for the same person + LLM output is idempotent
    at the edge level.
    """
    validate_edge("thread", "trait", "evidences")
    cursor.execute(
        """
        INSERT INTO edges (from_kind, from_id, to_kind, to_id,
                           edge_type, attributes)
        VALUES ('thread', %s, 'trait', %s, 'evidences', %s)
        ON CONFLICT (from_kind, from_id, to_kind, to_id, edge_type)
        DO NOTHING
        RETURNING id
        """,
        (thread_id, trait_id, Json({})),
    )
    return cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Post-commit embedding fan-out
# ---------------------------------------------------------------------------


def push_new_trait_embeddings(
    *,
    embedding_sender,
    new_traits: list[NewTraitRow],
    embedding_model: str,
    embedding_model_version: str,
) -> None:
    """Push one embedding job per newly inserted trait.

    Source text mirrors the convention used elsewhere: name plus
    comma + description if a description is present.
    """
    for trait in new_traits:
        if trait.description:
            source_text = f"{trait.name}, {trait.description}"
        else:
            source_text = trait.name
        embedding_sender.send(
            record_type="trait",
            record_id=trait.id,
            source_text=source_text,
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )
