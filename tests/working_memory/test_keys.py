"""Tests for the pure key-naming helpers."""

from __future__ import annotations

import pytest

from flashback.working_memory.keys import (
    InvalidSessionIdError,
    all_keys,
    segment_key,
    state_key,
    transcript_key,
)


class TestKeyShapes:
    def test_transcript_key_shape(self):
        assert transcript_key("abc-123") == "wm:session:abc-123:transcript"

    def test_segment_key_shape(self):
        assert segment_key("abc-123") == "wm:session:abc-123:segment"

    def test_state_key_shape(self):
        assert state_key("abc-123") == "wm:session:abc-123:state"

    def test_all_keys_returns_three(self):
        keys = all_keys("abc-123")
        assert keys == (
            "wm:session:abc-123:transcript",
            "wm:session:abc-123:segment",
            "wm:session:abc-123:state",
        )


class TestKeyValidation:
    def test_empty_session_id_rejected(self):
        with pytest.raises(InvalidSessionIdError):
            transcript_key("")

    def test_non_str_session_id_rejected(self):
        with pytest.raises(InvalidSessionIdError):
            transcript_key(None)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", ["with space", "new\nline", "tab\there"])
    def test_whitespace_in_session_id_rejected(self, bad: str):
        with pytest.raises(InvalidSessionIdError):
            state_key(bad)

    def test_uuid_passes(self):
        # Real UUIDs always pass.
        sid = "11111111-2222-3333-4444-555555555555"
        assert sid in transcript_key(sid)
