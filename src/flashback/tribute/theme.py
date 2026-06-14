"""Constants for the on-demand Tribute theme.

The tribute capability is one reusable theme (kind='tribute'), seeded
on demand when a contributor enters the flow -- NOT at person creation,
so normal legacies stay clean (spec section 4). 'Father's Day' is a copy
skin applied in Plan 4; the slug + neutral copy here are campaign-neutral.
"""

from __future__ import annotations

TRIBUTE_SLUG = "tribute"
TRIBUTE_DISPLAY_NAME = "A Tribute"
TRIBUTE_DESCRIPTION = (
    "A short, shareable tribute to them -- a handful of shared memories "
    "and one thing you'd want to say straight to them."
)

# Neutral default copy for the message-invitation tap. Plan 4's campaign
# skin (e.g. Father's Day) overrides this string.
MESSAGE_INVITATION_COPY = (
    "If you could say one thing straight to them, what would it be?"
)

# Expanded archetype question count for the tribute theme (universals
# stay at the 3-4 default).
TRIBUTE_ARCHETYPE_MIN = 6
TRIBUTE_ARCHETYPE_MAX = 8
