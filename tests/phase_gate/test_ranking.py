from __future__ import annotations

import pytest

from flashback.phase_gate.ranking import (
    SOURCE_PRIORITY,
    combined_score,
    diversity_score,
    source_priority_score,
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
