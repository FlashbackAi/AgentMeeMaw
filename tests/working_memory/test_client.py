"""Tests for the Valkey-backed WorkingMemory client.

Backed by fakeredis so the tests can run with no external service.
The Lua script in :meth:`update_rolling_summary` is exercised via
fakeredis's `[lua]` extra.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from flashback.working_memory.client import WorkingMemory, WorkingMemoryError
from flashback.working_memory.keys import segment_key, state_key, transcript_key

UTC = timezone.utc
SESSION_ID = "11111111-2222-3333-4444-555555555555"
PERSON_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ROLE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _now() -> datetime:
    return datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)


# --- initialise -------------------------------------------------------------


class TestInitialize:
    async def test_creates_state_hash_with_identity(self, wm: WorkingMemory, redis_client):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())

        raw = await redis_client.hgetall(state_key(SESSION_ID))
        assert raw[b"person_id"] == PERSON_ID.encode()
        assert raw[b"role_id"] == ROLE_ID.encode()
        assert raw[b"rolling_summary"] == b""
        assert raw[b"prior_rolling_summary"] == b""
        assert raw[b"signal_turns_in_current_segment"] == b"0"

    async def test_idempotent_does_not_overwrite(self, wm: WorkingMemory):
        """A second initialize call with a different person_id must not
        clobber the original state — Node never reissues a session_id, so
        if we see the key already exists, treat it as an idle refresh."""
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        await wm.append_turn(SESSION_ID, "user", "hello", _now())
        await wm.initialize(SESSION_ID, "other-person", "other-role", _now())

        state = await wm.get_state(SESSION_ID)
        assert state.person_id == PERSON_ID
        # Pre-existing transcript survives the second init.
        transcript = await wm.get_transcript(SESSION_ID)
        assert len(transcript) == 1

    async def test_seed_prior_session_summary(self, wm: WorkingMemory):
        await wm.initialize(
            SESSION_ID, PERSON_ID, ROLE_ID, _now(), seed_prior_session_summary="prev"
        )
        state = await wm.get_state(SESSION_ID)
        assert state.prior_session_summary == "prev"
        assert state.rolling_summary == ""
        assert state.prior_rolling_summary == ""

    async def test_exists_after_initialize(self, wm: WorkingMemory):
        assert not await wm.exists(SESSION_ID)
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        assert await wm.exists(SESSION_ID)


# --- append_turn / get_transcript / get_segment -----------------------------


class TestAppendTurn:
    async def test_appends_to_both_lists(self, wm: WorkingMemory):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        await wm.append_turn(SESSION_ID, "user", "hi", _now())

        transcript = await wm.get_transcript(SESSION_ID)
        segment = await wm.get_segment(SESSION_ID)
        assert len(transcript) == 1
        assert len(segment) == 1
        assert transcript[0].content == "hi"
        assert transcript[0].role == "user"

    async def test_chronological_order(self, wm: WorkingMemory):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        await wm.append_turn(SESSION_ID, "user", "first", _now())
        await wm.append_turn(SESSION_ID, "assistant", "second", _now())
        await wm.append_turn(SESSION_ID, "user", "third", _now())

        transcript = await wm.get_transcript(SESSION_ID)
        assert [t.content for t in transcript] == ["first", "second", "third"]
        assert [t.role for t in transcript] == ["user", "assistant", "user"]

    async def test_transcript_truncates_segment_does_not(self, wm: WorkingMemory):
        """35 turns -> transcript trims to 30 (the configured limit),
        segment still holds 35 (segment is bounded by boundary firing,
        not by the rolling window)."""
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        for i in range(35):
            await wm.append_turn(SESSION_ID, "user", f"msg-{i}", _now())

        transcript = await wm.get_transcript(SESSION_ID)
        segment = await wm.get_segment(SESSION_ID)
        assert len(transcript) == 30
        assert len(segment) == 35
        # The 30 surviving entries are the most recent ones (msg-5..msg-34).
        assert transcript[0].content == "msg-5"
        assert transcript[-1].content == "msg-34"


# --- reset_segment ----------------------------------------------------------


class TestResetSegment:
    async def test_reads_and_clears(self, wm: WorkingMemory):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        await wm.append_turn(SESSION_ID, "user", "a", _now())
        await wm.append_turn(SESSION_ID, "user", "b", _now())

        first = await wm.reset_segment(SESSION_ID)
        assert [t.content for t in first] == ["a", "b"]

        # Segment is now empty.
        again = await wm.reset_segment(SESSION_ID)
        assert again == []

        # Transcript untouched.
        transcript = await wm.get_transcript(SESSION_ID)
        assert len(transcript) == 2

    async def test_concurrent_reset_one_winner(self, wm: WorkingMemory):
        """Two concurrent reset_segment calls — only one sees the data,
        the other sees an empty list. Atomic at the Valkey level."""
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        for i in range(5):
            await wm.append_turn(SESSION_ID, "user", f"m-{i}", _now())

        a, b = await asyncio.gather(
            wm.reset_segment(SESSION_ID),
            wm.reset_segment(SESSION_ID),
        )
        # Exactly one of the calls saw the data.
        sizes = sorted([len(a), len(b)])
        assert sizes == [0, 5]


# --- update_rolling_summary -------------------------------------------------


class TestUpdateRollingSummary:
    async def test_promotes_current_to_prior(self, wm: WorkingMemory):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        await wm.update_rolling_summary(SESSION_ID, "v1")
        await wm.update_rolling_summary(SESSION_ID, "v2")
        state = await wm.get_state(SESSION_ID)
        assert state.rolling_summary == "v2"
        assert state.prior_rolling_summary == "v1"

    async def test_two_consecutive_updates(self, wm: WorkingMemory):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        await wm.update_rolling_summary(SESSION_ID, "v1")
        await wm.update_rolling_summary(SESSION_ID, "v2")
        state = await wm.get_state(SESSION_ID)
        assert state.rolling_summary == "v2"
        assert state.prior_rolling_summary == "v1"


# --- update_signals --------------------------------------------------------


class TestUpdateSignals:
    async def test_partial_update(self, wm: WorkingMemory):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        await wm.update_signals(
            SESSION_ID,
            signal_last_intent="recall",
            signal_emotional_temperature_estimate="medium",
        )
        state = await wm.get_state(SESSION_ID)
        assert state.signal_last_intent == "recall"
        assert state.signal_emotional_temperature_estimate == "medium"
        # Other signals unchanged.
        assert state.signal_turns_in_current_segment == 0
        # Identity unchanged.
        assert state.person_id == PERSON_ID

    async def test_empty_kwargs_noop(self, wm: WorkingMemory, redis_client):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        # Should not raise even without args.
        await wm.update_signals(SESSION_ID)
        state = await wm.get_state(SESSION_ID)
        assert state.person_id == PERSON_ID

    async def test_set_seeded_question(self, wm: WorkingMemory):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        await wm.set_seeded_question(SESSION_ID, "qid-42")
        state = await wm.get_state(SESSION_ID)
        assert state.last_seeded_question_id == "qid-42"

        await wm.set_seeded_question(SESSION_ID, None)
        state = await wm.get_state(SESSION_ID)
        assert state.last_seeded_question_id == ""


# --- TTL --------------------------------------------------------------------


class TestTTL:
    async def test_ttl_set_on_initialize(self, wm: WorkingMemory, redis_client):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        ttl = await redis_client.ttl(state_key(SESSION_ID))
        assert 0 < ttl <= 100

    async def test_ttl_set_after_append(self, wm: WorkingMemory, redis_client):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        await wm.append_turn(SESSION_ID, "user", "hi", _now())

        for key in (
            transcript_key(SESSION_ID),
            segment_key(SESSION_ID),
            state_key(SESSION_ID),
        ):
            ttl = await redis_client.ttl(key)
            assert 0 < ttl <= 100, f"missing TTL on {key}"

    async def test_ttl_refreshed_on_signal_update(self, wm: WorkingMemory, redis_client):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        # Push down the TTL artificially.
        await redis_client.expire(state_key(SESSION_ID), 5)
        await wm.update_signals(SESSION_ID, signal_last_intent="recall")
        ttl = await redis_client.ttl(state_key(SESSION_ID))
        assert ttl > 5  # refreshed back up to ~100


# --- clear ------------------------------------------------------------------


class TestClear:
    async def test_clear_deletes_all_three_keys(self, wm: WorkingMemory, redis_client):
        await wm.initialize(SESSION_ID, PERSON_ID, ROLE_ID, _now())
        await wm.append_turn(SESSION_ID, "user", "hi", _now())

        await wm.clear(SESSION_ID)
        assert not await redis_client.exists(transcript_key(SESSION_ID))
        assert not await redis_client.exists(segment_key(SESSION_ID))
        assert not await redis_client.exists(state_key(SESSION_ID))
        assert not await wm.exists(SESSION_ID)


# --- get_state error path ---------------------------------------------------


class TestGetStateMissing:
    async def test_raises_when_no_session(self, wm: WorkingMemory):
        with pytest.raises(WorkingMemoryError):
            await wm.get_state("nonexistent-session")
