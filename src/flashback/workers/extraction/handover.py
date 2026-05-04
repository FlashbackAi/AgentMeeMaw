"""
Handover Check (per CLAUDE.md §6 and ARCHITECTURE.md §3.11).

Flips ``persons.phase`` from ``starter`` to ``steady`` once every
coverage dimension is ≥ 1. Sticky: once a person is in ``steady``, the
check is a no-op. Admin can reset via the dedicated endpoint added in
step 4.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger("flashback.workers.extraction.handover")


def run_handover_check(cursor, *, person_id: str) -> bool:
    """Return True iff the person was flipped to steady on this call."""
    cursor.execute(
        """
        UPDATE persons
           SET phase = 'steady',
               phase_locked_at = now()
         WHERE id = %(pid)s
           AND phase = 'starter'
           AND (coverage_state->>'sensory')::int  >= 1
           AND (coverage_state->>'voice')::int    >= 1
           AND (coverage_state->>'place')::int    >= 1
           AND (coverage_state->>'relation')::int >= 1
           AND (coverage_state->>'era')::int      >= 1
        """,
        {"pid": person_id},
    )
    flipped = cursor.rowcount > 0
    if flipped:
        log.info("handover.flipped_to_steady", person_id=person_id)
    return flipped
