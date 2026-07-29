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


# Stories needed for the memories slot to read as FILLED and for the tribute
# to be READY (the raw qualifying-count gate; raised 3 -> 12 in migration
# 0051). The view's DISPLAYED percent is count-based against the same target
# since 0051 (the 0030 depth weighting is retired), with the archetype
# answer-floor still credited on campaign rows -- neither can flip
# "filled"/"ready" without 12 real qualifying moments. Exposed so the live
# meter can render "2 of 12 stories".
MEMORIES_TARGET = 12

# Archetype layers in the Father's Day bank (tribute/theme.py). The view's
# answer-floor divides answered_layers by this, so keep it in sync with the
# bank size if the bank grows. Exposed for "4 of 14 prompts answered" copy.
ARCHETYPE_LAYER_COUNT = 14


@dataclass(frozen=True)
class SlotMeta:
    key: str
    label: str
    hint: str
    weight: int


# Order = display order = steering priority (highest weight first).
# The `appearance` slot was retired as a SCORED slot (migration 0050): it
# capped the meter because the subject's physical ground truth rarely gets
# captured, and it deadlocked the message invitation. Appearance ground truth
# is still captured opportunistically and still feeds the image composers
# (ground_truth/render.py) -- it is simply no longer a checklist item. The
# remaining slots reweight to sum to 100 (campaign 50/35/15).
SLOTS: tuple[SlotMeta, ...] = (
    SlotMeta(
        key="memories",
        label="Shared memories",
        hint="Tell twelve stories about a time with them.",
        weight=50,
    ),
    SlotMeta(
        key="message",
        label="Your message",
        hint="Say one thing straight to them.",
        weight=35,
    ),
    SlotMeta(
        key="signature",
        label="What made them them",
        hint="A saying, a habit, or a trait of theirs.",
        weight=15,
    ),
)

SLOT_KEYS: tuple[str, ...] = tuple(s.key for s in SLOTS)
