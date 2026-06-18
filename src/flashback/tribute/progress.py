"""Read the tribute completion progress from the ``tribute_status`` view.

Pure read, no side effects. The view owns the filled/percent math; this
module decorates each slot with display copy from ``checklist.SLOTS`` so
internal callers (steering, assembly, the live meter) get a single typed
shape. Node reads the view directly and does not call this.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from flashback.tribute.campaigns import Campaign
from flashback.tribute.checklist import MEMORIES_TARGET, SLOTS
from flashback.tribute.theme import TRIBUTE_DISPLAY_NAME


@dataclass(frozen=True)
class TributeSlot:
    key: str
    label: str
    hint: str
    filled: bool
    # Granular progress for slots that fill incrementally. Only the
    # memories slot uses these today (count of qualifying stories vs the
    # target of 3); other slots leave them None and are purely binary.
    count: int | None = None
    target: int | None = None


@dataclass(frozen=True)
class TributeProgress:
    tribute_id: str
    percent: int
    ready: bool
    slots: list[TributeSlot]
    # Title for the meter header -- the campaign skin's display name
    # ("A Letter to Dad") or the neutral default ("A Tribute").
    title: str
    # Key of the first unfilled slot, or None when everything is filled.
    # Drives the "next -- ..." steer so the UI doesn't have to guess.
    next_key: str | None
    # Count of answered archetype layers credited toward the meter's
    # answer-floor (0030). Drives "4 of 14 prompts answered" meter copy.
    answered_layers: int = 0


_PROGRESS_SQL = """
SELECT memories_count, message_present, appearance_present,
       signature_present, percent, ready, answered_layers
  FROM tribute_status
 WHERE id = %(id)s
"""


def _decorate(
    row, *, tribute_id: UUID | str, campaign: Campaign | None
) -> TributeProgress:
    """Turn a raw ``tribute_status`` row into the decorated progress shape.

    Shared by the sync/async fetchers so slot copy + count/next derivation
    never drift between them. Campaign skin (if any) overrides the title
    and the message slot's hint copy; all other hints stay skin-neutral.
    """
    (
        memories_count,
        message_present,
        appearance_present,
        signature_present,
        percent,
        ready,
        answered_layers,
    ) = row

    filled_by_key = {
        "memories": memories_count >= MEMORIES_TARGET,
        "message": bool(message_present),
        "appearance": bool(appearance_present),
        "signature": bool(signature_present),
    }
    message_hint_override = campaign.message_card_copy if campaign else None
    slots: list[TributeSlot] = []
    for s in SLOTS:
        hint = (
            message_hint_override
            if s.key == "message" and message_hint_override
            else s.hint
        )
        slots.append(
            TributeSlot(
                key=s.key,
                label=s.label,
                hint=hint,
                filled=filled_by_key[s.key],
                count=int(memories_count) if s.key == "memories" else None,
                target=MEMORIES_TARGET if s.key == "memories" else None,
            )
        )
    next_key = next((s.key for s in slots if not s.filled), None)
    return TributeProgress(
        tribute_id=str(tribute_id),
        percent=int(percent),
        ready=bool(ready),
        slots=slots,
        title=campaign.display_name if campaign else TRIBUTE_DISPLAY_NAME,
        next_key=next_key,
        answered_layers=int(answered_layers or 0),
    )


def fetch_tribute_progress_sync(
    cur, *, tribute_id: UUID | str, campaign: Campaign | None = None
) -> TributeProgress | None:
    """Return the decorated progress for one tribute, or None if absent."""
    cur.execute(_PROGRESS_SQL, {"id": str(tribute_id)})
    row = cur.fetchone()
    if row is None:
        return None
    return _decorate(row, tribute_id=tribute_id, campaign=campaign)


async def fetch_tribute_progress_async(
    cur, *, tribute_id: UUID | str, campaign: Campaign | None = None
) -> TributeProgress | None:
    """Async twin of ``fetch_tribute_progress_sync``."""
    await cur.execute(_PROGRESS_SQL, {"id": str(tribute_id)})
    row = await cur.fetchone()
    if row is None:
        return None
    return _decorate(row, tribute_id=tribute_id, campaign=campaign)
