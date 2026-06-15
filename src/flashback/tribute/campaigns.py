"""Campaign skins for the tribute flow.

A skin is pure config + copy layered on the neutral tribute theme: it
overrides the display name + message-invitation copy + archetype framing
+ video target length, and marks a window where it's featured first in the
UX. 'Father's Day' is the launch skin; the neutral default exists
year-round. Skins never change behavior, only copy + a couple of numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from flashback.tribute.theme import (
    MESSAGE_INVITATION_COPY,
    TRIBUTE_DISPLAY_NAME,
    VIDEO_TARGET_SECONDS,
)


@dataclass(frozen=True)
class Campaign:
    slug: str
    display_name: str
    message_card_copy: str
    archetype_extra_context: str
    video_target_seconds: int
    featured: bool
    active_start: date | None
    active_end: date | None


NEUTRAL_CAMPAIGN = Campaign(
    slug="default",
    display_name=TRIBUTE_DISPLAY_NAME,
    message_card_copy=MESSAGE_INVITATION_COPY,
    archetype_extra_context="",
    video_target_seconds=VIDEO_TARGET_SECONDS,
    featured=False,
    active_start=None,
    active_end=None,
)

_CAMPAIGNS: dict[str, Campaign] = {
    "fathers_day_2026": Campaign(
        slug="fathers_day_2026",
        display_name="A Letter to Dad",
        message_card_copy=(
            "Fathers and sons don't always say it out loud. If he could "
            "hear one thing from you right now — what is it?"
        ),
        archetype_extra_context=(
            "This is a Father's Day tribute. Frame the questions around the "
            "subject as a father figure — what he was like, what he gave, "
            "the moments that stayed — while staying subject-status-agnostic."
        ),
        video_target_seconds=45,
        featured=True,
        active_start=date(2026, 6, 1),
        active_end=date(2026, 6, 22),
    ),
}


def resolve_campaign(slug: str | None) -> Campaign:
    """Return the campaign for a slug, or the neutral default."""
    if not slug or slug == "default":
        return NEUTRAL_CAMPAIGN
    return _CAMPAIGNS.get(slug, NEUTRAL_CAMPAIGN)


def list_campaigns() -> list[Campaign]:
    """Neutral first, then registered campaigns."""
    return [NEUTRAL_CAMPAIGN, *_CAMPAIGNS.values()]


def active_featured_campaign(today: date) -> Campaign | None:
    """The featured campaign whose window contains ``today``, if any."""
    for c in _CAMPAIGNS.values():
        if (
            c.featured
            and c.active_start is not None
            and c.active_end is not None
            and c.active_start <= today <= c.active_end
        ):
            return c
    return None
