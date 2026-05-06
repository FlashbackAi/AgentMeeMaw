"""Admin endpoints — currently just the phase reset escape hatch.

Per CLAUDE.md s6, the Handover Check is sticky. ``/admin/reset_phase``
flips a person back to ``starter``, clears ``phase_locked_at``, and
zeroes ``coverage_state`` in a single statement. It's the only way to
undo a Handover Check decision.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.http.auth import require_admin_service_token, require_service_token
from flashback.http.deps import get_db_pool
from flashback.http.models import ResetPhaseRequest, ResetPhaseResponse

router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_service_token), Depends(require_admin_service_token)],
)
log = structlog.get_logger("flashback.http.admin")

# CTE captures the pre-state in the same statement as the UPDATE so
# the response can echo back what the caller's request changed.
_RESET_PHASE_SQL = """
WITH prev AS (
    SELECT id, phase, phase_locked_at FROM persons WHERE id = %s
),
upd AS (
    UPDATE persons
    SET phase = 'starter',
        phase_locked_at = NULL,
        coverage_state =
            '{"sensory":0,"voice":0,"place":0,"relation":0,"era":0}'::jsonb
    WHERE id = %s
    RETURNING id
)
SELECT prev.phase, prev.phase_locked_at
FROM prev
JOIN upd USING (id)
"""


@router.post("/reset_phase", response_model=ResetPhaseResponse)
async def reset_phase(
    body: ResetPhaseRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> ResetPhaseResponse:
    structlog.contextvars.bind_contextvars(person_id=str(body.person_id))
    pid = str(body.person_id)
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_RESET_PHASE_SQL, (pid, pid))
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"person {body.person_id} not found",
        )

    previous_phase, previous_locked_at = row
    log.info("admin.reset_phase", previous_phase=previous_phase)
    return ResetPhaseResponse(
        person_id=body.person_id,
        previous_phase=previous_phase,
        previous_locked_at=(
            previous_locked_at.isoformat() if previous_locked_at else None
        ),
    )
