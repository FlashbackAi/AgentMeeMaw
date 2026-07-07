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
