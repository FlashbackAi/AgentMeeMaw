"""Themes step — ``themed_as`` edge validation.

The new edge type allows ``moment -> theme`` and only that direction.
The ``theme`` kind is added to ``VALID_KINDS`` for forward-compat with
future theme-sourced edges, but no other edge types accept ``theme``
as a source or target in v1.
"""

from __future__ import annotations

import pytest

from flashback.db.edges import (
    VALID_EDGE_PATTERNS,
    VALID_KINDS,
    EdgeValidationError,
    allowed_sources,
    allowed_targets,
    validate_edge,
)


def test_moment_to_theme_themed_as_accepted() -> None:
    assert validate_edge("moment", "theme", "themed_as") is None


def test_theme_to_moment_themed_as_rejected() -> None:
    with pytest.raises(EdgeValidationError):
        validate_edge("theme", "moment", "themed_as")


def test_entity_to_theme_themed_as_rejected() -> None:
    with pytest.raises(EdgeValidationError):
        validate_edge("entity", "theme", "themed_as")


def test_moment_to_thread_themed_as_rejected() -> None:
    with pytest.raises(EdgeValidationError):
        validate_edge("moment", "thread", "themed_as")


def test_theme_in_valid_kinds() -> None:
    assert "theme" in VALID_KINDS


def test_themed_as_pattern_is_only_moment_theme() -> None:
    assert VALID_EDGE_PATTERNS["themed_as"] == frozenset({("moment", "theme")})


def test_themed_as_allowed_targets_only_theme_from_moment() -> None:
    assert allowed_targets("moment", "themed_as") == frozenset({"theme"})


def test_themed_as_allowed_sources_only_moment_for_theme() -> None:
    assert allowed_sources("theme", "themed_as") == frozenset({"moment"})


def test_no_other_edge_type_accepts_theme() -> None:
    """No v1 edge type other than ``themed_as`` should have ``theme`` in
    either position."""
    for edge_type, patterns in VALID_EDGE_PATTERNS.items():
        if edge_type == "themed_as":
            continue
        for from_kind, to_kind in patterns:
            assert from_kind != "theme", (
                f"{edge_type!r} unexpectedly allows theme as source"
            )
            assert to_kind != "theme", (
                f"{edge_type!r} unexpectedly allows theme as target"
            )
