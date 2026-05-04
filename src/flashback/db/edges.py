"""
Edge validation for the canonical graph.

The agent's `edges` table is a generic relationship store. The allowed
combinations of (from_kind, to_kind) for each edge_type are enforced
in app code rather than via DB constraints — this keeps the schema
flat and the rules in one inspectable place.

EVERY write to `edges` MUST go through validate_edge() before the INSERT.

Things this module does NOT enforce (caller's responsibility):
  * person_id scoping — every node referenced must belong to the same legacy.
  * Existence and active status of referenced rows.
  * Sub-kind constraints — e.g. `happened_at` requires the entity to be
    kind='place'. We can't check entity kind without a DB read, so the
    Extraction Worker (or whoever writes the edge) must verify.
"""

from __future__ import annotations

# ----------------------------------------------------------------------------
# Allowed (from_kind, to_kind) tuples per edge_type.
#
# Edges are directional — ordering matters even when the relationship is
# conceptually symmetric (e.g., related_to between two entities).
# ----------------------------------------------------------------------------

VALID_EDGE_PATTERNS: dict[str, frozenset[tuple[str, str]]] = {
    "involves": frozenset({
        ("moment", "entity"),
    }),
    "happened_at": frozenset({
        ("moment", "entity"),  # entity must be kind='place' (caller-enforced)
    }),
    "exemplifies": frozenset({
        ("moment", "trait"),
    }),
    "evidences": frozenset({
        ("moment", "thread"),
        ("entity", "thread"),
        # Step 13 (Trait Synthesizer):
        ("thread", "trait"),
        # forward-compat for a future entity-level evidences writer; no
        # caller in v1.
        ("entity", "trait"),
    }),
    "related_to": frozenset({
        ("entity", "entity"),
    }),
    "motivated_by": frozenset({
        ("question", "moment"),
        ("question", "entity"),
        ("question", "thread"),
    }),
    "targets": frozenset({
        ("question", "entity"),
    }),
    "answered_by": frozenset({
        ("question", "moment"),
    }),
}

VALID_KINDS: frozenset[str] = frozenset({
    "moment", "entity", "thread", "trait", "question", "person",
})


class EdgeValidationError(ValueError):
    """Raised when an edge violates the allowed pattern matrix."""


def validate_edge(from_kind: str, to_kind: str, edge_type: str) -> None:
    """
    Validate a (from_kind, to_kind, edge_type) tuple.

    Raises EdgeValidationError if the combination is not allowed.
    Returns None on success.

    This function is intentionally narrow: it validates only the
    structural shape of the edge. Callers must separately ensure:
      * referenced rows exist and are status='active'
      * referenced rows belong to the same person_id (legacy scope)
      * sub-kind requirements (e.g., happened_at -> place entity)
    """
    if edge_type not in VALID_EDGE_PATTERNS:
        raise EdgeValidationError(
            f"Unknown edge_type: {edge_type!r}. "
            f"Valid types: {sorted(VALID_EDGE_PATTERNS)}"
        )
    if from_kind not in VALID_KINDS:
        raise EdgeValidationError(
            f"Unknown from_kind: {from_kind!r}. "
            f"Valid kinds: {sorted(VALID_KINDS)}"
        )
    if to_kind not in VALID_KINDS:
        raise EdgeValidationError(
            f"Unknown to_kind: {to_kind!r}. "
            f"Valid kinds: {sorted(VALID_KINDS)}"
        )

    allowed = VALID_EDGE_PATTERNS[edge_type]
    if (from_kind, to_kind) not in allowed:
        raise EdgeValidationError(
            f"edge_type {edge_type!r} does not allow "
            f"{from_kind!r} -> {to_kind!r}. "
            f"Allowed: {sorted(allowed)}"
        )


def allowed_targets(from_kind: str, edge_type: str) -> frozenset[str]:
    """
    Return the set of valid `to_kind` values for a given
    (from_kind, edge_type). Useful for query-builder helpers
    or validation in higher-level write APIs.
    """
    if edge_type not in VALID_EDGE_PATTERNS:
        return frozenset()
    return frozenset(
        to_k for (f_k, to_k) in VALID_EDGE_PATTERNS[edge_type] if f_k == from_kind
    )


def allowed_sources(to_kind: str, edge_type: str) -> frozenset[str]:
    """
    Return the set of valid `from_kind` values for a given
    (to_kind, edge_type).
    """
    if edge_type not in VALID_EDGE_PATTERNS:
        return frozenset()
    return frozenset(
        f_k for (f_k, to_k) in VALID_EDGE_PATTERNS[edge_type] if to_k == to_kind
    )
