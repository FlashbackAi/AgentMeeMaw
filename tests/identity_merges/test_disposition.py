"""Pure unit tests for the auto-merge / ask / drop disposition policy.

No DB or LLM — runs in any environment.
"""

from __future__ import annotations

import pytest

from flashback.identity_merges.disposition import decide_disposition


@pytest.mark.parametrize(
    ("verdict", "confidence", "expected"),
    [
        # same_identity routes by confidence
        ("same_identity", "high", "auto_merge"),
        ("same_identity", "medium", "ask"),
        ("same_identity", "low", "drop"),
        # unsure always asks (needs a human) regardless of confidence
        ("unsure", "high", "ask"),
        ("unsure", "medium", "ask"),
        ("unsure", "low", "ask"),
        # different_identity always drops
        ("different_identity", "high", "drop"),
        ("different_identity", "medium", "drop"),
        ("different_identity", "low", "drop"),
    ],
)
def test_disposition_matrix(verdict, confidence, expected):
    assert decide_disposition(verdict, confidence) == expected


def test_unknown_verdict_is_conservative():
    assert decide_disposition("garbage", "high") == "drop"


def test_only_high_confidence_same_identity_auto_merges():
    """The risky action (silent merge) requires the strongest signal."""
    auto = [
        (v, c)
        for v in ("same_identity", "different_identity", "unsure")
        for c in ("low", "medium", "high")
        if decide_disposition(v, c) == "auto_merge"
    ]
    assert auto == [("same_identity", "high")]
