"""Display metadata for the tribute completion checklist.

The FILLED / PERCENT computation lives in the ``tribute_status`` SQL view
(the surface Node reads directly), so the weights never drift between
Python and SQL. This module holds only the user-facing slot ORDER + COPY,
consumed by steering (Plan 2) and the live meter (Plan 4). The labels here
are the neutral default skin; campaign skins (Plan 4) may override
``label`` / ``hint`` per slot. ``weight`` is duplicated here purely as
documentation of the view's weighting and for steering priority -- the
SQL view remains the source of truth for the actual percent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlotMeta:
    key: str
    label: str
    hint: str
    weight: int


# Order = display order = steering priority (highest weight first).
SLOTS: tuple[SlotMeta, ...] = (
    SlotMeta(
        key="memories",
        label="Shared memories",
        hint="Tell three stories about a time with them.",
        weight=40,
    ),
    SlotMeta(
        key="message",
        label="Your message",
        hint="Say one thing straight to them.",
        weight=30,
    ),
    SlotMeta(
        key="appearance",
        label="How they looked",
        hint="A few details so we can picture them.",
        weight=20,
    ),
    SlotMeta(
        key="signature",
        label="What made them them",
        hint="A saying, a habit, or a trait of theirs.",
        weight=10,
    ),
)

SLOT_KEYS: tuple[str, ...] = tuple(s.key for s in SLOTS)
