"""Steady-phase question selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from flashback.phase_gate.queries import SELECT_RECENT_THEMES, SELECT_STEADY_CANDIDATES
from flashback.phase_gate.ranking import combined_score
from flashback.phase_gate.schema import SelectionResult
from flashback.working_memory import WorkingMemory

UNIVERSAL_DIMENSION_DEMOTION_GAP = 1.5
PRODUCER_SOURCES: tuple[str, ...] = (
    "dropped_reference",
    "underdeveloped_entity",
    "thread_deepen",
    "life_period_gap",
    "universal_dimension",
)
STEADY_SOURCES: tuple[str, ...] = PRODUCER_SOURCES
STARTER_FALLBACK_SOURCES: tuple[str, ...] = (
    "underdeveloped_entity",
    "life_period_gap",
    "universal_dimension",
)


class SteadySelector:
    def __init__(self, db_pool: AsyncConnectionPool, working_memory: WorkingMemory):
        self._pool = db_pool
        self._wm = working_memory

    async def select(
        self,
        person_id: UUID,
        session_id: UUID,
        *,
        sources: tuple[str, ...] = STEADY_SOURCES,
        active_theme_slug: str | None = None,
        last_seeded_source: str | None = None,
        current_user_id: UUID | None = None,
    ) -> SelectionResult:
        """Pick the next-best question from the person's bank.

        Three-step fallback for the candidate pool:
          1. Try the requested sources minus ``last_seeded_source`` (same-
             source cooldown), with skipped questions excluded.
          2. If empty, drop the same-source cooldown.
          3. If still empty, drop the skipped-exclusion (skip fallback per
             invariant: better to repeat than crash when the bank exhausts).

        The recency term in :func:`combined_score` is fed
        ``datetime.now(timezone.utc)`` so older questions decay against
        fresh ones. Deferred questions get an additive ``DEFER_BOOST``.
        """
        recent_ids = [
            UUID(question_id)
            for question_id in await self._wm.get_recently_asked_question_ids(
                str(session_id)
            )
        ]
        effective_sources: tuple[str, ...] = (
            tuple(s for s in sources if s != last_seeded_source) or sources
        )
        recent_themes = await self._fetch_recent_themes(recent_ids)

        used_source_cooldown_fallback = False
        used_skip_fallback = False

        candidates = await self._fetch_candidates(
            person_id, recent_ids, effective_sources, exclude_skipped=True,
            current_user_id=current_user_id,
        )
        if not candidates and effective_sources != sources:
            candidates = await self._fetch_candidates(
                person_id, recent_ids, sources, exclude_skipped=True,
                current_user_id=current_user_id,
            )
            used_source_cooldown_fallback = bool(candidates)
        if not candidates:
            candidates = await self._fetch_candidates(
                person_id, recent_ids, sources, exclude_skipped=False,
                current_user_id=current_user_id,
            )
            used_skip_fallback = bool(candidates)
        if not candidates:
            return SelectionResult(
                phase="steady",
                rationale="steady bank empty; no seeded question",
            )

        now = datetime.now(timezone.utc)
        scored = [
            _ScoredCandidate(
                candidate=candidate,
                score=combined_score(
                    candidate.source,
                    candidate.themes,
                    recent_themes,
                    active_theme_slug=active_theme_slug,
                    created_at=candidate.created_at,
                    now=now,
                    is_deferred=candidate.decision_action == "defer",
                ),
            )
            for candidate in candidates
        ]
        scored.sort(
            key=lambda item: (item.score, item.candidate.created_at),
            reverse=True,
        )
        selected = _apply_universal_dimension_demotion(scored)
        candidate = selected.candidate
        rationale_suffix = ""
        if used_skip_fallback:
            rationale_suffix = " (fallback to skipped)"
        elif used_source_cooldown_fallback:
            rationale_suffix = " (fallback: source cooldown dropped)"
        return SelectionResult(
            phase="steady",
            question_id=candidate.id,
            question_text=candidate.text,
            source=candidate.source,
            dimension=None,
            rationale=(
                f"steady selected {candidate.source}; "
                f"score={selected.score:.3f}; recent_themes={len(recent_themes)}"
                f"{rationale_suffix}"
            ),
        )

    async def _fetch_recent_themes(self, recent_ids: list[UUID]) -> set[str]:
        if not recent_ids:
            return set()
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    SELECT_RECENT_THEMES,
                    {"question_ids": recent_ids},
                )
                row = await cur.fetchone()
        if row is None or row[0] is None:
            return set()
        return {str(theme) for theme in row[0] if theme}

    async def _fetch_candidates(
        self,
        person_id: UUID,
        recent_ids: list[UUID],
        sources: tuple[str, ...],
        *,
        exclude_skipped: bool,
        current_user_id: UUID | None = None,
    ) -> list["_Candidate"]:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    SELECT_STEADY_CANDIDATES,
                    {
                        "person_id": person_id,
                        "recent_ids": recent_ids,
                        "sources": list(sources),
                        "exclude_skipped": exclude_skipped,
                        "current_user_id": current_user_id,
                    },
                )
                rows = await cur.fetchall()
        return [
            _Candidate(
                id=row[0],
                text=row[1],
                source=row[2],
                attributes=row[3] if isinstance(row[3], dict) else {},
                created_at=row[4],
                decision_action=row[5],
                decision_decided_at=row[6],
            )
            for row in rows
        ]


@dataclass(frozen=True)
class _Candidate:
    id: UUID
    text: str
    source: str
    attributes: dict[str, Any]
    created_at: datetime
    decision_action: str | None = None
    decision_decided_at: datetime | None = None

    @property
    def themes(self) -> set[str]:
        raw = self.attributes.get("themes", [])
        if not isinstance(raw, list):
            return set()
        return {str(theme) for theme in raw if theme}


@dataclass(frozen=True)
class _ScoredCandidate:
    candidate: _Candidate
    score: float


def _apply_universal_dimension_demotion(
    scored: list[_ScoredCandidate],
) -> _ScoredCandidate:
    selected = scored[0]
    if selected.candidate.source != "universal_dimension":
        return selected
    for candidate in scored[1:]:
        if candidate.candidate.source == "universal_dimension":
            continue
        if selected.score - candidate.score <= UNIVERSAL_DIMENSION_DEMOTION_GAP:
            return candidate
        break
    return selected
