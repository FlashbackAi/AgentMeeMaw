"""Constants for the on-demand Tribute theme.

The tribute capability is one reusable theme (kind='tribute'), seeded
on demand when a contributor enters the flow -- NOT at person creation,
so normal legacies stay clean (spec section 4). Campaign skins and
relationship-profile registers live in Postgres (tribute CRM, migration
0039); the slug + neutral copy here are campaign-neutral. The Father's
Day archetype bank moved into the fathers_day_2026 campaign seed row
(archetype_bank_override) in that migration.
"""

from __future__ import annotations

TRIBUTE_SLUG = "tribute"
TRIBUTE_DISPLAY_NAME = "A Tribute"
TRIBUTE_DESCRIPTION = (
    "A short, shareable tribute to them -- a handful of shared memories "
    "and one thing you'd want to say straight to them."
)

# Neutral default copy for the message-invitation tap. A campaign's
# message_card_copy or a relationship profile's message_invitation_copy
# (both DB config) override this string.
MESSAGE_INVITATION_COPY = (
    "If you could say one thing straight to them, what would it be?"
)

# Expanded archetype question count for the tribute theme (universals
# stay at the 3-4 default). Authored banks (a campaign's override or a
# relationship profile's bank, both in Postgres per migration 0039) are
# served WHOLE and bypass these bounds; they only gate the LLM-generated
# tribute path. Wide because the tribute gathers a lot of material up front.
TRIBUTE_ARCHETYPE_MIN = 8
TRIBUTE_ARCHETYPE_MAX = 22

# Compiled-output shape (Plan 3). Video length is skin-configurable in
# the campaign config; this is the neutral default. Storybook is hard-capped
# with a floor below which it won't generate. Cap raised to 13 (cover + up
# to 12 scenes) so a full chronological confession arc has room to breathe --
# the prior 9 compressed a 15-beat story too hard. Scene count still scales
# down with the graph: the assembler only emits scenes it has vivid material
# for.
VIDEO_TARGET_SECONDS = 45
STORYBOOK_MIN_PAGES = 3
STORYBOOK_MAX_PAGES = 13
