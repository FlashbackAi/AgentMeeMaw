from __future__ import annotations

import pytest

from flashback.phase_gate.ranking import (
    SOURCE_PRIORITY,
    THEME_BIAS_WEIGHT,
    combined_score,
    diversity_score,
    source_priority_score,
    theme_bias_score,
)


def test_source_priority_score_known_sources():
    expected = {
        "dropped_reference": 4.0,
        "underdeveloped_entity": 3.0,
        "thread_deepen": 2.0,
        "life_period_gap": 1.0,
        "universal_dimension": 0.0,
    }
    assert set(SOURCE_PRIORITY) == set(expected)
    for source, score in expected.items():
        assert source_priority_score(source) == score


def test_source_priority_score_unknown_source():
    assert source_priority_score("surprise") == 0.0


@pytest.mark.parametrize(
    ("question_themes", "recent_themes", "expected"),
    [
        (set(), {"family"}, 0.0),
        ({"family"}, {"family"}, 0.0),
        ({"family"}, {"work"}, 1.0),
        ({"family", "ritual"}, {"ritual", "place"}, 0.5),
    ],
)
def test_diversity_score(question_themes, recent_themes, expected):
    assert diversity_score(question_themes, recent_themes) == expected


def test_combined_score():
    assert combined_score(
        "underdeveloped_entity",
        {"family", "ritual"},
        {"ritual"},
    ) == 4.0


def test_theme_bias_score_no_active_theme():
    assert theme_bias_score({"family", "ritual"}, None) == 0.0
    assert theme_bias_score({"family", "ritual"}, "") == 0.0


def test_theme_bias_score_with_match():
    assert theme_bias_score({"family", "ritual"}, "family") == 1.0


def test_theme_bias_score_without_match():
    assert theme_bias_score({"family", "ritual"}, "cricket") == 0.0


def test_theme_bias_score_empty_question_themes():
    assert theme_bias_score(set(), "family") == 0.0


def test_combined_score_applies_theme_bias():
    """Active theme overlapping a candidate's themes adds the bias term."""
    baseline = combined_score(
        "thread_deepen",
        {"family"},
        set(),  # no recent themes -> diversity = 1.0
        active_theme_slug=None,
    )
    biased = combined_score(
        "thread_deepen",
        {"family"},
        set(),
        active_theme_slug="family",
    )
    assert biased == pytest.approx(baseline + THEME_BIAS_WEIGHT)


def test_combined_score_theme_bias_skips_when_no_overlap():
    score_without_overlap = combined_score(
        "thread_deepen",
        {"career"},
        set(),
        active_theme_slug="family",
    )
    score_no_theme = combined_score(
        "thread_deepen",
        {"career"},
        set(),
        active_theme_slug=None,
    )
    assert score_without_overlap == score_no_theme
