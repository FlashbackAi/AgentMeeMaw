"""Phase Gate router."""

from __future__ import annotations

from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from flashback.phase_gate.queries import READ_PERSON_PHASE
from flashback.phase_gate.schema import Phase, PhaseGateError, SelectionResult
from flashback.phase_gate.steady_selector import STARTER_FALLBACK_SOURCES, SteadySelector


class PhaseGate:
    def __init__(
        self,
        db_pool: AsyncConnectionPool,
        steady_selector: SteadySelector,
    ) -> None:
        self._pool = db_pool
        self._steady = steady_selector

    async def select_next_question(
        self,
        person_id: UUID,
        session_id: UUID,
        recently_asked_ids: list[UUID] | None = None,
        active_theme_slug: str | None = None,
    ) -> SelectionResult:
        """Read ``persons.phase`` and select from the runtime question bank.

        ``recently_asked_ids`` carries the session-scoped Working Memory
        register for callers that still pass it. The steady selector reads
        the same list internally for duplicate avoidance and diversity.

        ``active_theme_slug`` (if set, e.g. during a deepen session) adds
        a soft bias to candidates whose ``attributes.themes`` overlaps.
        Never a hard filter — see CLAUDE.md theme spec.
        """
        phase = await self._read_phase(person_id)
        if phase == "starter":
            result = await self._steady.select(
                person_id,
                session_id,
                sources=STARTER_FALLBACK_SOURCES,
                active_theme_slug=active_theme_slug,
            )
        else:
            result = await self._steady.select(
                person_id,
                session_id,
                active_theme_slug=active_theme_slug,
            )
        result.phase = phase
        result.rationale = result.rationale or f"{phase} selection"
        return result

    async def _read_phase(self, person_id: UUID) -> Phase:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(READ_PERSON_PHASE, {"person_id": person_id})
                row = await cur.fetchone()
        if row is None:
            raise PhaseGateError(f"person {person_id} not found")
        phase = row[0]
        if phase not in {"starter", "steady"}:
            raise PhaseGateError(f"person {person_id} has invalid phase {phase!r}")
        return phase
