"""
Coverage Tracker (per CLAUDE.md §6 and ARCHITECTURE.md §3.10).

Runs inside the extraction transaction, immediately after persistence.
For each new moment, increments the appropriate dimensions in
``persons.coverage_state``. Counters are allowed to climb past 1; the
Handover Check only cares about the ≥ 1 threshold per dimension.
"""

from __future__ import annotations

import structlog

from .persistence import MomentCoverageSignal

log = structlog.get_logger("flashback.workers.extraction.coverage")


def run_coverage_tracker(
    cursor, *, person_id: str, moment_signals: list[MomentCoverageSignal]
) -> dict[str, int]:
    """
    Apply coverage deltas for a batch of newly-written moments.

    Returns the deltas applied for logging. The actual counter values
    after the UPDATE live in the database; we don't read them back here
    because the Handover Check (next step) does its own atomic check.
    """
    deltas = {
        "sensory": 0,
        "voice": 0,
        "place": 0,
        "relation": 0,
        "era": 0,
    }
    for sig in moment_signals:
        if sig.has_sensory:
            deltas["sensory"] += 1
        if sig.has_voice:
            deltas["voice"] += 1
        if sig.has_place:
            deltas["place"] += 1
        if sig.has_non_subject_person:
            deltas["relation"] += 1
        if sig.has_era:
            deltas["era"] += 1

    if not any(deltas.values()):
        log.info("coverage.no_increments", person_id=person_id)
        return deltas

    cursor.execute(
        """
        UPDATE persons
           SET coverage_state = jsonb_build_object(
                 'sensory',  COALESCE((coverage_state->>'sensory')::int, 0)
                             + %(s)s,
                 'voice',    COALESCE((coverage_state->>'voice')::int, 0)
                             + %(v)s,
                 'place',    COALESCE((coverage_state->>'place')::int, 0)
                             + %(p)s,
                 'relation', COALESCE((coverage_state->>'relation')::int, 0)
                             + %(r)s,
                 'era',      COALESCE((coverage_state->>'era')::int, 0)
                             + %(e)s
               )
         WHERE id = %(pid)s
        """,
        {
            "s": deltas["sensory"],
            "v": deltas["voice"],
            "p": deltas["place"],
            "r": deltas["relation"],
            "e": deltas["era"],
            "pid": person_id,
        },
    )
    log.info("coverage.applied", person_id=person_id, **deltas)
    return deltas
