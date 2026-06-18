"""Seam inputs for serving the fixed FD archetype bank from unlock_prepare."""

from __future__ import annotations

from datetime import date

from flashback.tribute.campaigns import active_featured_campaign
from flashback.tribute.theme import build_fathers_day_archetype_questions


def test_fd_window_is_active_on_launch_date() -> None:
    # The route keys the fixed-bank short-circuit on the active campaign window.
    assert active_featured_campaign(date(2026, 6, 18)) is not None


def test_active_fd_campaign_requests_confession_voice() -> None:
    c = active_featured_campaign(date(2026, 6, 18))
    assert c is not None and c.confession_voice is True


def test_fd_bank_questions_are_usable_mc() -> None:
    qs = build_fathers_day_archetype_questions()
    assert qs and all(q.text and len(q.options) >= 2 for q in qs)
