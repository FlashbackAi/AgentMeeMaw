from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from flashback.phase_gate.ranking import (
    DEFER_BOOST,
    RECENCY_WEIGHT,
    SOURCE_PRIORITY,
    THEME_BIAS_WEIGHT,
    combined_score,
    diversity_score,
    recency_score,
    source_priority_score,
    theme_bias_score,
)

NOW = datetime(2026, 5, 17, tzinfo=timezone.utc)


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


def test_combined_score_fresh_baseline():
    # underdeveloped_entity (priority 3) + diversity 0.5 * 2.0 = 4.0
    # + recency 1.0 (fresh) * 2.5 = 6.5
    assert combined_score(
        "underdeveloped_entity",
        {"family", "ritual"},
        {"ritual"},
        created_at=NOW,
        now=NOW,
    ) == pytest.approx(6.5)


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
    baseline = combined_score(
        "thread_deepen",
        {"family"},
        set(),
        active_theme_slug=None,
        created_at=NOW,
        now=NOW,
    )
    biased = combined_score(
        "thread_deepen",
        {"family"},
        set(),
        active_theme_slug="family",
        created_at=NOW,
        now=NOW,
    )
    assert biased == pytest.approx(baseline + THEME_BIAS_WEIGHT)


def test_combined_score_theme_bias_skips_when_no_overlap():
    score_without_overlap = combined_score(
        "thread_deepen",
        {"career"},
        set(),
        active_theme_slug="family",
        created_at=NOW,
        now=NOW,
    )
    score_no_theme = combined_score(
        "thread_deepen",
        {"career"},
        set(),
        active_theme_slug=None,
        created_at=NOW,
        now=NOW,
    )
    assert score_without_overlap == score_no_theme


def test_recency_score_fresh_is_one():
    assert recency_score(NOW, NOW) == 1.0


def test_recency_score_decays():
    age_30d = recency_score(NOW - timedelta(days=30), NOW)
    age_90d = recency_score(NOW - timedelta(days=90), NOW)
    assert 0.30 < age_30d < 0.45
    assert age_90d < 0.10


def test_fresh_lower_tier_beats_old_higher_tier_when_age_delta_large():
    """A 90-day-old underdeveloped_entity should lose to a freshly produced life_period_gap."""
    old_high = combined_score(
        "underdeveloped_entity",
        set(),
        set(),
        active_theme_slug=None,
        created_at=NOW - timedelta(days=90),
        now=NOW,
        is_deferred=False,
    )
    fresh_low = combined_score(
        "life_period_gap",
        set(),
        set(),
        active_theme_slug=None,
        created_at=NOW,
        now=NOW,
        is_deferred=False,
    )
    assert fresh_low > old_high


def test_defer_boost_makes_deferred_outrank_same_tier_fresh():
    deferred = combined_score(
        "universal_dimension",
        set(),
        set(),
        active_theme_slug=None,
        created_at=NOW - timedelta(days=30),
        now=NOW,
        is_deferred=True,
    )
    fresh = combined_score(
        "universal_dimension",
        set(),
        set(),
        active_theme_slug=None,
        created_at=NOW,
        now=NOW,
        is_deferred=False,
    )
    assert deferred > fresh


def test_source_priority_still_dominant_when_age_equal():
    fresh_high = combined_score(
        "dropped_reference",
        set(),
        set(),
        active_theme_slug=None,
        created_at=NOW,
        now=NOW,
    )
    fresh_low = combined_score(
        "universal_dimension",
        set(),
        set(),
        active_theme_slug=None,
        created_at=NOW,
        now=NOW,
    )
    assert fresh_high > fresh_low


def test_defer_boost_is_constant():
    """DEFER_BOOST is a flat additive bonus."""
    assert DEFER_BOOST > 0


def test_recency_weight_matches_tier_swing():
    """RECENCY_WEIGHT is large enough that fresh can swing past one tier."""
    # 1 source priority point + RECENCY_WEIGHT (fresh boost) > 1 tier alone
    assert RECENCY_WEIGHT >= 1.0
