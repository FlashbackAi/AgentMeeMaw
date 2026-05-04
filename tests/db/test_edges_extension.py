"""Step 13 — assertions for the new ``evidences`` edge tuples.

The Trait Synthesizer writes ``thread → trait`` evidence edges; the
``entity → trait`` shape is added forward-compatibly for a future
producer. Both must be accepted by ``validate_edge``. Pre-existing
tuples must continue to work, and reverse directions must still raise.
"""

from __future__ import annotations

import pytest

from flashback.db.edges import (
    VALID_EDGE_PATTERNS,
    EdgeValidationError,
    allowed_sources,
    allowed_targets,
    validate_edge,
)


# ---------------------------------------------------------------------------
# New tuples accepted
# ---------------------------------------------------------------------------


def test_thread_to_trait_evidences_accepted() -> None:
    # Returns None on success; raising would fail the test.
    assert validate_edge("thread", "trait", "evidences") is None


def test_entity_to_trait_evidences_accepted() -> None:
    assert validate_edge("entity", "trait", "evidences") is None


# ---------------------------------------------------------------------------
# Pre-existing tuples still accepted (no regression)
# ---------------------------------------------------------------------------


def test_moment_to_thread_evidences_still_accepted() -> None:
    assert validate_edge("moment", "thread", "evidences") is None


def test_entity_to_thread_evidences_still_accepted() -> None:
    assert validate_edge("entity", "thread", "evidences") is None


# ---------------------------------------------------------------------------
# Reverse directions still rejected
# ---------------------------------------------------------------------------


def test_trait_to_thread_evidences_rejected() -> None:
    with pytest.raises(EdgeValidationError):
        validate_edge("trait", "thread", "evidences")


def test_trait_to_entity_evidences_rejected() -> None:
    with pytest.raises(EdgeValidationError):
        validate_edge("trait", "entity", "evidences")


# ---------------------------------------------------------------------------
# Helpers reflect the new tuples too
# ---------------------------------------------------------------------------


def test_allowed_targets_includes_trait_for_thread_and_entity() -> None:
    assert "trait" in allowed_targets("thread", "evidences")
    assert "trait" in allowed_targets("entity", "evidences")
    # Old targets unchanged
    assert "thread" in allowed_targets("moment", "evidences")
    assert "thread" in allowed_targets("entity", "evidences")


def test_allowed_sources_for_trait_evidences() -> None:
    sources = allowed_sources("trait", "evidences")
    assert "thread" in sources
    assert "entity" in sources
    # Trait is never a source for evidences (it's always the target).
    assert "trait" not in sources


def test_evidences_pattern_matrix_unchanged_otherwise() -> None:
    assert VALID_EDGE_PATTERNS["evidences"] == frozenset({
        ("moment", "thread"),
        ("entity", "thread"),
        ("thread", "trait"),
        ("entity", "trait"),
    })
