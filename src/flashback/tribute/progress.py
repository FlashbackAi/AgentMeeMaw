"""Read the tribute completion progress from the ``tribute_status`` view.

Pure read, no side effects. The view owns the filled/percent math; this
module decorates each slot with display copy from ``checklist.SLOTS`` so
internal callers (steering, assembly, the live meter) get a single typed
shape. Node reads the view directly and does not call this.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from flashback.tribute.checklist import SLOTS


@dataclass(frozen=True)
class TributeSlot:
    key: str
    label: str
    hint: str
    filled: bool


@dataclass(frozen=True)
class TributeProgress:
    tribute_id: str
    percent: int
    ready: bool
    slots: list[TributeSlot]


_PROGRESS_SQL = """
SELECT memories_count, message_present, appearance_present,
       signature_present, percent, ready
  FROM tribute_status
 WHERE id = %(id)s
"""


def fetch_tribute_progress_sync(
    cur, *, tribute_id: UUID | str
) -> TributeProgress | None:
    """Return the decorated progress for one tribute, or None if absent."""
    cur.execute(_PROGRESS_SQL, {"id": str(tribute_id)})
    row = cur.fetchone()
    if row is None:
        return None
    (
        memories_count,
        message_present,
        appearance_present,
        signature_present,
        percent,
        ready,
    ) = row

    filled_by_key = {
        "memories": memories_count >= 3,
        "message": bool(message_present),
        "appearance": bool(appearance_present),
        "signature": bool(signature_present),
    }
    slots = [
        TributeSlot(key=s.key, label=s.label, hint=s.hint, filled=filled_by_key[s.key])
        for s in SLOTS
    ]
    return TributeProgress(
        tribute_id=str(tribute_id),
        percent=int(percent),
        ready=bool(ready),
        slots=slots,
    )


async def fetch_tribute_progress_async(
    cur, *, tribute_id: UUID | str
) -> TributeProgress | None:
    """Async twin of ``fetch_tribute_progress_sync``."""
    await cur.execute(_PROGRESS_SQL, {"id": str(tribute_id)})
    row = await cur.fetchone()
    if row is None:
        return None
    (
        memories_count,
        message_present,
        appearance_present,
        signature_present,
        percent,
        ready,
    ) = row
    filled_by_key = {
        "memories": memories_count >= 3,
        "message": bool(message_present),
        "appearance": bool(appearance_present),
        "signature": bool(signature_present),
    }
    slots = [
        TributeSlot(key=s.key, label=s.label, hint=s.hint, filled=filled_by_key[s.key])
        for s in SLOTS
    ]
    return TributeProgress(
        tribute_id=str(tribute_id),
        percent=int(percent),
        ready=bool(ready),
        slots=slots,
    )
