"""Starter-phase question selection."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from flashback.phase_gate.queries import (
    HAS_ACTIVE_MOMENTS,
    READ_COVERAGE_STATE,
    READ_PERSON_NAME,
    SELECT_ANY_STARTER_FOR_DIMENSION,
    SELECT_UNANSWERED_STARTER,
)
from flashback.phase_gate.ranking import TIEBREAKER_DIMENSIONS
from flashback.phase_gate.schema import Dimension, PhaseGateError, SelectionResult


class StarterSelector:
    def __init__(self, db_pool: AsyncConnectionPool):
        self._pool = db_pool

    async def select(self, person_id: UUID) -> SelectionResult:
        """Pick a starter_anchor template for the given person."""
        dimension = await self._choose_dimension(person_id)
        row = await self._fetch_template(
            person_id=person_id,
            dimension=dimension,
            exclude_answered=True,
        )
        answered_filter_used = True
        if row is None:
            row = await self._fetch_template(
                person_id=person_id,
                dimension=dimension,
                exclude_answered=False,
            )
            answered_filter_used = False
        if row is None:
            raise PhaseGateError(
                f"no active starter_anchor templates for dimension {dimension!r}"
            )

        question_id, text = row
        rendered_text = text.replace("{name}", await self._read_name(person_id))
        filter_note = "unanswered template" if answered_filter_used else "fallback"
        return SelectionResult(
            phase="starter",
            question_id=question_id,
            question_text=rendered_text,
            source="starter_anchor",
            dimension=dimension,
            rationale=f"starter {filter_note}; selected {dimension}",
        )

    async def _read_name(self, person_id: UUID) -> str:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(READ_PERSON_NAME, {"person_id": person_id})
                row = await cur.fetchone()
        if row is None:
            raise PhaseGateError(f"person {person_id} not found")
        return str(row[0])

    async def _choose_dimension(self, person_id: UUID) -> Dimension:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(HAS_ACTIVE_MOMENTS, {"person_id": person_id})
                moments_row = await cur.fetchone()
                if moments_row is None:
                    raise PhaseGateError(f"person {person_id} not found")
                has_moments = bool(moments_row[0])
                if not has_moments:
                    return "sensory"

                await cur.execute(READ_COVERAGE_STATE, {"person_id": person_id})
                coverage_row = await cur.fetchone()

        if coverage_row is None:
            raise PhaseGateError(f"person {person_id} not found")
        return _lowest_coverage_dimension(coverage_row[0])

    async def _fetch_template(
        self,
        *,
        person_id: UUID,
        dimension: Dimension,
        exclude_answered: bool,
    ) -> tuple[UUID, str] | None:
        query = (
            SELECT_UNANSWERED_STARTER
            if exclude_answered
            else SELECT_ANY_STARTER_FOR_DIMENSION
        )
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    query,
                    {"person_id": person_id, "dimension": dimension},
                )
                row = await cur.fetchone()
        if row is None:
            return None
        question_id, text = row
        return (question_id, text)


def _lowest_coverage_dimension(coverage_state: Any) -> Dimension:
    if not isinstance(coverage_state, dict):
        coverage_state = {}

    counts = {
        dim: _coverage_count(coverage_state.get(dim, 0))
        for dim in TIEBREAKER_DIMENSIONS
    }
    lowest = min(counts.values())
    for dim in TIEBREAKER_DIMENSIONS:
        if counts[dim] == lowest:
            return cast(Dimension, dim)
    raise PhaseGateError("could not choose starter dimension")


def _coverage_count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
