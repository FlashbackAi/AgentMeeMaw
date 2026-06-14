"""Campaign registry: resolution, neutral fallback, featured window."""

from __future__ import annotations

from datetime import date

from flashback.tribute.campaigns import (
    NEUTRAL_CAMPAIGN,
    active_featured_campaign,
    resolve_campaign,
)


def test_resolve_unknown_or_none_is_neutral() -> None:
    assert resolve_campaign(None) is NEUTRAL_CAMPAIGN
    assert resolve_campaign("default") is NEUTRAL_CAMPAIGN
    assert resolve_campaign("nope") is NEUTRAL_CAMPAIGN


def test_resolve_fathers_day_overrides_copy_and_length() -> None:
    c = resolve_campaign("fathers_day_2026")
    assert c.slug == "fathers_day_2026"
    assert c.display_name == "A Letter to Dad"
    assert "say it" in c.message_card_copy.lower()
    assert c.video_target_seconds == 45


def test_active_featured_only_inside_window() -> None:
    assert active_featured_campaign(date(2026, 6, 15)) is not None
    assert active_featured_campaign(date(2026, 1, 1)) is None
    assert active_featured_campaign(date(2026, 12, 25)) is None
