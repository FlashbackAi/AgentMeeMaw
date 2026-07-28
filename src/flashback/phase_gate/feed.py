"""Read-only ranked feed of a person's producer-bank questions.

Reuses the SteadySelector fetch + ranking so the feed's ordering matches
what the bot would pick next. Applies the invariant #10 universal-
dimension spread across the returned slice, then caps. No LLM, no session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from flashback.phase_gate.steady_selector import (
    PRODUCER_SOURCES,
    Candidate,
    ScoredCandidate,
    fetch_steady_candidates,
    rank_candidates,
)

DEFAULT_LIMIT = 25
MAX_LIMIT = 50


@dataclass(frozen=True)
class FeedQuestion:
    question_id: UUID
    text: str
    source: str
    themes: list[str]
    created_at: datetime


def spread_universal_dimension(
    scored: list[ScoredCandidate],
    *,
    window: int = 5,
    max_per_window: int = 1,
) -> list[ScoredCandidate]:
    """Reorder so no window of ``window`` consecutive positions holds more
    than ``max_per_window`` ``universal_dimension`` items. Never drops."""
    universals = [
        sc for sc in scored if sc.candidate.source == "universal_dimension"
    ]
    others = [
        sc for sc in scored if sc.candidate.source != "universal_dimension"
    ]
    if not universals:
        return list(scored)

    out: list[ScoredCandidate] = []
    ui = 0
    oi = 0
    while ui < len(universals) or oi < len(others):
        recent = out[-window + 1:] if window > 1 else []
        recent_universals = sum(
            1 for sc in recent if sc.candidate.source == "universal_dimension"
        )
        can_place_universal = recent_universals < max_per_window
        if oi < len(others) and (
            not can_place_universal or ui >= len(universals)
        ):
            out.append(others[oi])
            oi += 1
        elif ui < len(universals) and can_place_universal:
            out.append(universals[ui])
            ui += 1
        elif oi < len(others):
            out.append(others[oi])
            oi += 1
        else:
            # Only universals remain and the window is saturated; append the
            # rest in rank order (tail spread is best-effort — never drop).
            out.extend(universals[ui:])
            ui = len(universals)
    return out


class QuestionFeed:
    def __init__(self, db_pool: AsyncConnectionPool) -> None:
        self._pool = db_pool

    async def build(
        self, person_id: UUID, *, limit: int = DEFAULT_LIMIT
    ) -> list[FeedQuestion]:
        limit = max(1, min(limit, MAX_LIMIT))
        candidates: list[Candidate] = await fetch_steady_candidates(
            self._pool,
            person_id,
            [],
            PRODUCER_SOURCES,
            exclude_skipped=True,
        )
        ranked = rank_candidates(
            candidates,
            recent_themes=set(),
            active_theme_slug=None,
            now=datetime.now(timezone.utc),
        )
        spread = spread_universal_dimension(ranked)
        return [
            FeedQuestion(
                question_id=sc.candidate.id,
                text=sc.candidate.text,
                source=sc.candidate.source,
                themes=sorted(sc.candidate.themes),
                created_at=sc.candidate.created_at,
            )
            for sc in spread[:limit]
        ]
