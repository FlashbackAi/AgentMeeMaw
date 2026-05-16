"""Theme context fields on WorkingMemoryState round-trip cleanly."""

from __future__ import annotations

from datetime import datetime, timezone

from flashback.working_memory.schema import (
    WorkingMemoryState,
    parse_state_hash,
    serialise_state_for_init,
)


def test_default_theme_fields_are_empty() -> None:
    state = WorkingMemoryState(
        person_id="p1",
        role_id="r1",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert state.current_theme_id == ""
    assert state.current_theme_slug == ""
    assert state.current_theme_display_name == ""


def test_theme_fields_round_trip_via_serialise_parse() -> None:
    state = WorkingMemoryState(
        person_id="p1",
        role_id="r1",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        current_theme_id="t-123",
        current_theme_slug="family",
        current_theme_display_name="Family",
    )
    raw = serialise_state_for_init(state)
    assert raw["current_theme_id"] == "t-123"
    assert raw["current_theme_slug"] == "family"
    assert raw["current_theme_display_name"] == "Family"

    parsed = parse_state_hash(raw)
    assert parsed.current_theme_id == "t-123"
    assert parsed.current_theme_slug == "family"
    assert parsed.current_theme_display_name == "Family"
