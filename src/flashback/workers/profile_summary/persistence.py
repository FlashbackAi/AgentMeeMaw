"""Per-person persistence for the Profile Summary Generator.

The unit of work is one person. Inside a single transaction:

  1. UPDATE ``persons.profile_summary`` (and bump ``updated_at``).
  2. INSERT into ``processed_profile_summaries`` for idempotency.

If anything raises mid-way, the surrounding transaction rolls back and
the SQS message is not acked — SQS visibility timeout will redrive.

No history table for profile summaries. Overwrites are fine; nothing
load-bearing is lost (the source data — moments, threads, entities,
traits — persists).

No embedding push. Profile summaries are display only and are not
embedded.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from .idempotency import mark_processed

log = structlog.get_logger("flashback.workers.profile_summary.persistence")


@dataclass
class PersistResult:
    """What the worker needs after a successful commit."""

    summary_chars: int

    def summary(self) -> dict:
        """Compact dict used in worker log lines."""
        return {"summary_chars": self.summary_chars}


def persist_summary(
    cursor,
    *,
    person_id: str,
    summary_text: str,
    idempotency_key: str,
) -> PersistResult:
    """Run the full transactional write. Caller owns BEGIN/COMMIT/ROLLBACK.

    The caller is expected to have already verified the person exists
    (the context-build step does this implicitly via ``_fetch_person``).
    If the UPDATE matches zero rows we still write the idempotency row
    — that case shouldn't happen in practice, but matching trait_synth
    we choose not to fail here.
    """
    cursor.execute(
        """
        UPDATE persons
           SET profile_summary = %s,
               updated_at      = now()
         WHERE id = %s
        """,
        (summary_text, person_id),
    )

    mark_processed(
        cursor,
        idempotency_key=idempotency_key,
        person_id=person_id,
        summary_chars=len(summary_text),
    )

    return PersistResult(summary_chars=len(summary_text))
