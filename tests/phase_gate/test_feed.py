from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from flashback.phase_gate.steady_selector import Candidate, rank_candidates

NOW = datetime(2026, 5, 4, tzinfo=timezone.utc)


def _cand(qid: str, source: str, created_at: datetime, themes=None) -> Candidate:
    return Candidate(
        id=UUID(qid),
        text=f"q-{qid[:4]}",
        source=source,
        attributes={"themes": themes or []},
        created_at=created_at,
    )


def test_rank_candidates_orders_by_score_desc():
    old_high = _cand(
        "33333333-3333-3333-3333-333333333333",
        "dropped_reference",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    fresh_low = _cand(
        "44444444-4444-4444-4444-444444444444",
        "universal_dimension",
        NOW,
    )
    ranked = rank_candidates(
        [fresh_low, old_high],
        recent_themes=set(),
        active_theme_slug=None,
        now=NOW,
    )
    assert [sc.candidate.id for sc in ranked] == [old_high.id, fresh_low.id]
    assert ranked[0].score >= ranked[1].score


from flashback.phase_gate.feed import (  # noqa: E402
    FeedQuestion,
    QuestionFeed,
    spread_universal_dimension,
)
from flashback.phase_gate.steady_selector import ScoredCandidate  # noqa: E402


def _scored(qid_char: str, source: str, score: float) -> ScoredCandidate:
    qid = (
        qid_char * 8 + "-" + qid_char * 4 + "-" + qid_char * 4
        + "-" + qid_char * 4 + "-" + qid_char * 12
    )
    return ScoredCandidate(
        candidate=_cand(qid, source, NOW),
        score=score,
    )


def test_spread_universal_dimension_no_two_in_window_of_five():
    # Worst case for clustering: the 3 universal_dimension items are ranked
    # highest, ahead of enough non-universals to interleave them apart.
    universals = [
        _scored("1", "universal_dimension", 9.0),
        _scored("2", "universal_dimension", 8.0),
        _scored("3", "universal_dimension", 7.0),
    ]
    others = [
        _scored(c, "underdeveloped_entity", 6.0 - i)
        for i, c in enumerate("4567890abc")
    ]
    scored = universals + others
    out = spread_universal_dimension(scored, window=5, max_per_window=1)
    # With enough non-universals available, no window of 5 consecutive
    # positions holds more than one universal_dimension.
    for start in range(0, max(1, len(out) - 4)):
        window = out[start:start + 5]
        n_universal = sum(
            1 for sc in window if sc.candidate.source == "universal_dimension"
        )
        assert n_universal <= 1
    # No item is dropped and no phantom item appears.
    assert len(out) == len(scored)


def test_spread_universal_dimension_never_drops_when_universals_dominate():
    # Universals outnumber the gaps: the spread cannot fully separate them,
    # but it must never drop or duplicate an item.
    scored = [
        _scored("1", "universal_dimension", 9.0),
        _scored("2", "universal_dimension", 8.0),
        _scored("3", "universal_dimension", 7.0),
        _scored("4", "underdeveloped_entity", 6.0),
        _scored("5", "universal_dimension", 5.0),
    ]
    out = spread_universal_dimension(scored, window=5, max_per_window=1)
    assert len(out) == len(scored)
    assert {sc.candidate.id for sc in out} == {sc.candidate.id for sc in scored}


class _FeedPool:
    def __init__(self, rows):
        self._rows = rows

    def connection(self):
        return _FeedCtx(_FeedConn(self._rows))


class _FeedConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FeedCtx(_FeedCursor(self._rows))


class _FeedCursor:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, sql, params=None):
        self._sql = sql

    async def fetchall(self):
        return self._rows


class _FeedCtx:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


async def test_question_feed_build_caps_and_maps():
    rows = [
        (
            UUID("33333333-3333-3333-3333-333333333333"),
            "Tell me about the bike.",
            "dropped_reference",
            {"themes": ["family"]},
            NOW,
            None,
            None,
        ),
        (
            UUID("44444444-4444-4444-4444-444444444444"),
            "What was your first job?",
            "life_period_gap",
            {"themes": ["career"]},
            NOW,
            None,
            None,
        ),
    ]
    feed = QuestionFeed(_FeedPool(rows))
    result = await feed.build(
        UUID("11111111-1111-1111-1111-111111111111"), limit=1
    )
    assert len(result) == 1
    assert isinstance(result[0], FeedQuestion)
    assert result[0].source == "dropped_reference"
    assert result[0].themes == ["family"]
