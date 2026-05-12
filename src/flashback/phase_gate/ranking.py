"""Deterministic ranking helpers for Phase Gate question selection."""

from __future__ import annotations

DIVERSITY_WEIGHT: float = 2.0
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


def combined_score(
    source: str,
    question_themes: set[str],
    recent_themes: set[str],
) -> float:
    return (
        source_priority_score(source)
        + DIVERSITY_WEIGHT * diversity_score(question_themes, recent_themes)
    )
