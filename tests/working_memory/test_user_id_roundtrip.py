"""user_id replaces role_id in WM state (spec D1)."""

from datetime import datetime, timezone

from flashback.working_memory.schema import (
    WorkingMemoryState,
    parse_state_hash,
    serialise_state_for_init,
)


def test_user_id_round_trips_through_serialise_and_parse():
    state = WorkingMemoryState(
        person_id="p1",
        user_id="11111111-1111-1111-1111-111111111111",
        started_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )
    mapping = serialise_state_for_init(state)
    assert mapping["user_id"] == "11111111-1111-1111-1111-111111111111"
    assert "role_id" not in mapping

    parsed = parse_state_hash(mapping)
    assert parsed.user_id == "11111111-1111-1111-1111-111111111111"


def test_user_id_defaults_to_empty_string():
    # Sessions started before Node sends user_id hydrate with "".
    state = WorkingMemoryState(
        person_id="p1",
        started_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )
    assert state.user_id == ""


def test_parse_state_hash_drops_legacy_role_id_key():
    # A live session started before the rename still has role_id in its
    # Valkey HASH; hydration must not crash (extra="forbid" on the model).
    state = WorkingMemoryState(
        person_id="p1",
        started_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )
    mapping = serialise_state_for_init(state)
    mapping["role_id"] = "some-stale-uuid"
    parsed = parse_state_hash(mapping)
    assert parsed.user_id == ""
