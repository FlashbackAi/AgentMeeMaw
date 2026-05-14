"""Phase Gate router."""

from __future__ import annotations

from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from flashback.phase_gate.queries import READ_PERSON_PHASE
from flashback.phase_gate.schema import Phase, PhaseGateError, SelectionResult
from flashback.phase_gate.starter_selector import StarterSelector
from flashback.phase_gate.steady_selector import SteadySelector


class PhaseGate:
    def __init__(
        self,
        db_pool: AsyncConnectionPool,
        starter_selector: StarterSelector,
        steady_selector: SteadySelector,
    ) -> None:
        self._pool = db_pool
        self._starter = starter_selector
        self._steady = steady_selector

    async def select_starter_question(self, person_id: UUID) -> SelectionResult:
        """Always use starter selection, regardless of stored phase."""
        result = await self._starter.select(person_id)
        result.rationale = result.rationale or "starter selection"
        result.phase = "starter"
        return result

    async def select_next_question(
        self,
        person_id: UUID,
        session_id: UUID,
        recently_asked_ids: list[UUID] | None = None,
    ) -> SelectionResult:
        """Read ``persons.phase`` and route to starter or steady selection.

        ``recently_asked_ids`` carries the session-scoped Working Memory
        register so neither starter nor steady selection re-serves a
        question already asked this session. The steady selector also
        reads the same list internally for theme diversity, so for now
        pass it explicitly to keep call-sites symmetric.
        """
        phase = await self._read_phase(person_id)
        if phase == "starter":
            result = await self._starter.select(
                person_id, recently_asked_ids=recently_asked_ids
            )
        else:
            result = await self._steady.select(person_id, session_id)
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
