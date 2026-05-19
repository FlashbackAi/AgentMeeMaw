"""Deterministic ranking helpers for Phase Gate question selection."""

from __future__ import annotations

import math
from datetime import datetime

DIVERSITY_WEIGHT: float = 2.0
RECENCY_WEIGHT: float = 2.5
DEFER_BOOST: float = 2.0
RECENCY_HALF_LIFE_DAYS: float = 30.0
RECENTLY_ASKED_WINDOW: int = 5

SOURCE_PRIORITY: tuple[str, ...] = (
    "dropped_reference",
    "underdeveloped_entity",
    "thread_deepen",
    "life_period_gap",
    "universal_dimension",
)
TIEBREAKER_DIMENSIONS: tuple[str, ...] = (
    "era",
    "relation",
    "place",
    "voice",
    "sensory",
)

# Bonus added to a candidate's combined_score when its themes overlap
# with the active deepen-session theme. Soft bias only — large enough
# to break ties in favor of theme-aligned questions but small enough
# that a high-priority source (dropped_reference) on a different theme
# still wins.
THEME_BIAS_WEIGHT: float = 1.5


def source_priority_score(source: str) -> float:
    """Higher is better. ``dropped_reference`` = 4.0; unknowns = 0.0."""
    try:
        rank = SOURCE_PRIORITY.index(source)
    except ValueError:
        return 0.0
    return float(len(SOURCE_PRIORITY) - 1 - rank)


def diversity_score(question_themes: set[str], recent_themes: set[str]) -> float:
    """Return 1 - (|themes(q) intersection recent_themes| / |themes(q)|)."""
    if not question_themes:
        return 0.0
    overlap = len(question_themes & recent_themes)
    return 1.0 - (overlap / len(question_themes))


def theme_bias_score(
    question_themes: set[str], active_theme_slug: str | None
) -> float:
    """Return 1.0 when any of the question's themes match the active
    deepen-session theme slug, else 0.0. Multiplied by THEME_BIAS_WEIGHT
    in :func:`combined_score`."""
    if not active_theme_slug:
        return 0.0
    if not question_themes:
        return 0.0
    return 1.0 if active_theme_slug in question_themes else 0.0


def recency_score(created_at: datetime, now: datetime) -> float:
    """Exponential decay over time. Fresh = 1.0; 30 days = ~0.37; 90 days = ~0.05."""
    delta = now - created_at
    days = max(0.0, delta.total_seconds() / 86400.0)
    return math.exp(-days / RECENCY_HALF_LIFE_DAYS)


def combined_score(
    source: str,
    question_themes: set[str],
    recent_themes: set[str],
    *,
    active_theme_slug: str | None = None,
    created_at: datetime,
    now: datetime,
    is_deferred: bool = False,
) -> float:
    return (
        source_priority_score(source)
        + DIVERSITY_WEIGHT * diversity_score(question_themes, recent_themes)
        + THEME_BIAS_WEIGHT * theme_bias_score(question_themes, active_theme_slug)
        + RECENCY_WEIGHT * recency_score(created_at, now)
        + (DEFER_BOOST if is_deferred else 0.0)
    )
