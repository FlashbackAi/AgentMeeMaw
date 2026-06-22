"""collaborator_onboarding_tap_emitted WM flag — defaults False, round-trips through hash."""

from __future__ import annotations

from datetime import datetime, timezone

from flashback.working_memory.schema import (
    WorkingMemoryState,
    parse_state_hash,
    serialise_state_for_init,
)


def _base_state() -> WorkingMemoryState:
    return WorkingMemoryState(
        person_id="p",
        user_id="u",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_flag_defaults_false() -> None:
    s = _base_state()
    assert s.collaborator_onboarding_tap_emitted is False


def test_flag_round_trips_through_hash() -> None:
    base = serialise_state_for_init(_base_state())
    assert "collaborator_onboarding_tap_emitted" in base
    hydrated = parse_state_hash({**base, "collaborator_onboarding_tap_emitted": "True"})
    assert hydrated.collaborator_onboarding_tap_emitted is True
