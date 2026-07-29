"""Read the tribute completion progress from the ``tribute_status`` view.

Pure read, no side effects. The view owns the filled/percent math; this
module decorates each slot with display copy from ``checklist.SLOTS`` so
internal callers (steering, assembly, the live meter) get a single typed
shape. Node reads the view directly and does not call this.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from flashback.tribute.checklist import MEMORIES_TARGET, SLOTS
from flashback.tribute.config_schema import CampaignConfig as Campaign
from flashback.tribute.theme import TRIBUTE_DISPLAY_NAME


@dataclass(frozen=True)
class TributeSlot:
    key: str
    label: str
    hint: str
    filled: bool
    # Granular progress for slots that fill incrementally. Only the
    # memories slot uses these today (count of qualifying stories vs
    # MEMORIES_TARGET); other slots leave them None and are purely binary.
    count: int | None = None
    target: int | None = None


@dataclass(frozen=True)
class TributeProgress:
    tribute_id: str
    percent: int
    ready: bool
    # 'standalone' (the always-on keepsake, no message slot) or 'campaign'
    # (the occasion, with the message slot). Two-meter model, design 2026-07-22.
    kind: str
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
       signature_present, percent, ready, answered_layers, meter_kind
  FROM tribute_status
 WHERE id = %(id)s
"""

# Owner-scoped variant: returns no row when the tribute belongs to a
# different person, so the HTTP route gets a clean 404 for free instead
# of leaking another legacy's progress.
_PROGRESS_SCOPED_SQL = _PROGRESS_SQL + " AND person_id = %(person_id)s"


def progress_to_payload(p: TributeProgress) -> dict:
    """Serialize the decorated progress into the live-meter JSON shape.

    Single source of truth for the meter payload: both the /turn metadata
    (orchestrator) and the standalone GET /tributes/{id}/progress endpoint
    render through here so their shapes never drift.
    """
    return {
        "percent": p.percent,
        "ready": p.ready,
        "kind": p.kind,
        "title": p.title,
        "next": p.next_key,
        "slots": [
            {
                "key": s.key,
                "label": s.label,
                "hint": s.hint,
                "filled": s.filled,
                "count": s.count,
                "target": s.target,
            }
            for s in p.slots
        ],
    }


def _decorate(
    row,
    *,
    tribute_id: UUID | str,
    campaign: Campaign | None,
    message_hint_override: str | None = None,
) -> TributeProgress:
    """Turn a raw ``tribute_status`` row into the decorated progress shape.

    Shared by the sync/async fetchers so slot copy + count/next derivation
    never drift between them. ``message_hint_override`` carries the fully
    resolved invitation question (campaign -> profile -> neutral, see
    flashback.tribute.invitation); when absent, the campaign's copy (if
    any) keeps the pre-CRM behavior. All other hints stay skin-neutral.
    """
    (
        memories_count,
        message_present,
        appearance_present,
        signature_present,
        percent,
        ready,
        answered_layers,
        meter_kind,
    ) = row

    kind = meter_kind or ("campaign" if campaign else "standalone")

    # `appearance` is retired as a scored slot (migration 0050) -- not in
    # SLOTS, so never emitted. appearance_present is still read from the view
    # (and kept as a column for Node) but no longer drives the meter.
    _ = appearance_present
    filled_by_key = {
        "memories": memories_count >= MEMORIES_TARGET,
        "message": bool(message_present),
        "signature": bool(signature_present),
    }
    if message_hint_override is None:
        message_hint_override = campaign.message_card_copy if campaign else None
    slots: list[TributeSlot] = []
    for s in SLOTS:
        # Standalone has no message slot (simplified keepsake) -- skip it so its
        # meter is stories + appearance + signature only.
        if s.key == "message" and kind != "campaign":
            continue
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
        kind=kind,
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
    cur,
    *,
    tribute_id: UUID | str,
    campaign: Campaign | None = None,
    person_id: UUID | str | None = None,
    message_hint_override: str | None = None,
) -> TributeProgress | None:
    """Async twin of ``fetch_tribute_progress_sync``.

    When ``person_id`` is supplied the lookup is owner-scoped: a tribute
    belonging to a different person returns None (the route turns that into
    a 404). Omit it for the internal live-meter read, which is already
    scoped by the session's ``current_tribute_id``.
    """
    if person_id is not None:
        await cur.execute(
            _PROGRESS_SCOPED_SQL,
            {"id": str(tribute_id), "person_id": str(person_id)},
        )
    else:
        await cur.execute(_PROGRESS_SQL, {"id": str(tribute_id)})
    row = await cur.fetchone()
    if row is None:
        return None
    return _decorate(
        row,
        tribute_id=tribute_id,
        campaign=campaign,
        message_hint_override=message_hint_override,
    )
