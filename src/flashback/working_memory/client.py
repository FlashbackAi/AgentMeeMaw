"""
Valkey-backed Working Memory client.

The contract is laid out in ARCHITECTURE.md s3.4 and the step-4 prompt.
All operations are async and refresh the TTL on every key they touch
(invariant #7 — sessions are short-lived; the TTL is just GC for
orphaned keys, but the lease must be extended on activity so an active
session does not expire mid-conversation).

Atomicity:

* :meth:`reset_segment` reads the segment list and deletes it inside a
  single MULTI/EXEC pipeline. Two concurrent calls are serialised by
  Valkey; the second one returns an empty list.
* :meth:`update_rolling_summary` promotes ``rolling_summary`` to
  ``prior_rolling_summary`` and sets the new value via a small Lua
  script (EVAL). This avoids a read-modify-write race against another
  writer.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Literal

from redis.asyncio import Redis

from flashback.working_memory.keys import (
    all_keys,
    asked_key,
    segment_key,
    state_key,
    transcript_key,
)
from flashback.working_memory.schema import (
    Turn,
    WorkingMemoryState,
    parse_state_hash,
    serialise_state_for_init,
)

# Lua script — atomically promote rolling_summary -> prior_rolling_summary
# and set rolling_summary to ARGV[1]. EXPIRE refreshes the lease.
#   KEYS[1] = state hash key
#   ARGV[1] = new rolling summary
#   ARGV[2] = ttl seconds
_UPDATE_ROLLING_SUMMARY_LUA = """
local current = redis.call('HGET', KEYS[1], 'rolling_summary')
if current == false then current = '' end
redis.call('HSET', KEYS[1], 'prior_rolling_summary', current)
redis.call('HSET', KEYS[1], 'rolling_summary', ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""


class WorkingMemoryError(RuntimeError):
    """Raised on internal Working Memory invariant violations."""


class WorkingMemory:
    """
    Per-session ephemeral state, keyed by ``session_id``.

    The same instance is shared across all HTTP handlers — it's a thin
    wrapper around the Redis async client and is safe to reuse
    concurrently (redis-py's async client is connection-pooled).
    """

    def __init__(
        self,
        redis_client: Redis,
        ttl_seconds: int,
        transcript_limit: int,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if transcript_limit <= 0:
            raise ValueError("transcript_limit must be positive")
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._transcript_limit = transcript_limit

    # --- Lifecycle ---------------------------------------------------------

    async def initialize(
        self,
        session_id: str,
        person_id: str,
        role_id: str,
        started_at: datetime,
        seed_prior_session_summary: str = "",
        contributor_display_name: str = "",
        current_theme_id: str = "",
        current_theme_slug: str = "",
        current_theme_display_name: str = "",
    ) -> None:
        """
        Create WM for a new session.

        ``seed_prior_session_summary`` is read-only cross-session context
        (Node-supplied previous-session summary, or a continuity
        snapshot built from the canonical graph). It lives in
        ``prior_session_summary`` and is consumed only by the response
        generator. ``rolling_summary`` is born empty so the segment
        detector and extraction worker never see content from a prior
        session as in-session context.

        ``contributor_display_name`` is the contributor's display name
        passed by Node on every ``/session/start``. It is read-only,
        stored only for archive-side workers (extraction, trait
        synthesizer, profile summary, thread detector) so they can
        attribute naturally. It is **never** exposed to the response
        generator or opener — the chat surface stays neutral.

        Idempotent: calling twice with the same args is safe. We do NOT
        wipe an existing session — Node never re-issues the same
        session_id, so a duplicate call is treated as a no-op refresh
        of the TTL on the already-initialised state.
        """
        s_key = state_key(session_id)
        # Has it been created already?
        existing = await self._redis.exists(s_key)
        if existing:
            # Already there; just refresh the lease on all three keys.
            await self._refresh_ttls(session_id)
            return

        state = WorkingMemoryState(
            person_id=person_id,
            role_id=role_id,
            started_at=started_at,
            prior_session_summary=seed_prior_session_summary,
            contributor_display_name=contributor_display_name,
            current_theme_id=current_theme_id,
            current_theme_slug=current_theme_slug,
            current_theme_display_name=current_theme_display_name,
        )
        mapping = serialise_state_for_init(state)
        async with self._redis.pipeline(transaction=True) as p:
            p.hset(s_key, mapping=mapping)
            p.expire(s_key, self._ttl)
            await p.execute()

    async def exists(self, session_id: str) -> bool:
        """True iff the state hash exists. Transcript / segment may be
        empty even for a live session, so the state hash is the
        canonical existence check."""
        return bool(await self._redis.exists(state_key(session_id)))

    async def clear(self, session_id: str) -> None:
        """Delete all three keys. Called at session wrap *after* the
        force-close has read the final segment buffer."""
        keys = all_keys(session_id)
        await self._redis.delete(*keys)

    # --- Turn append / read ------------------------------------------------

    async def append_turn(
        self,
        session_id: str,
        role: Literal["user", "assistant"],
        content: str,
        timestamp: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Append to BOTH transcript and segment buffer.

        Trim transcript to the last ``transcript_limit`` entries. The
        segment buffer is *not* trimmed; it grows until a boundary
        fires, at which point :meth:`reset_segment` clears it.
        """
        turn = Turn(role=role, content=content, timestamp=timestamp, metadata=metadata or {})
        payload = turn.to_json()
        t_key = transcript_key(session_id)
        seg_key = segment_key(session_id)
        s_key = state_key(session_id)

        async with self._redis.pipeline(transaction=True) as p:
            p.rpush(t_key, payload)
            # Keep only the LAST N — LTRIM with negative indices.
            p.ltrim(t_key, -self._transcript_limit, -1)
            p.rpush(seg_key, payload)
            p.expire(t_key, self._ttl)
            p.expire(seg_key, self._ttl)
            p.expire(s_key, self._ttl)
            await p.execute()

    async def get_transcript(self, session_id: str) -> list[Turn]:
        """Return the rolling transcript window (oldest first)."""
        raw = await self._redis.lrange(transcript_key(session_id), 0, -1)
        return [Turn.from_json(r) for r in raw]

    async def get_segment(self, session_id: str) -> list[Turn]:
        """Return the current segment buffer (oldest first)."""
        raw = await self._redis.lrange(segment_key(session_id), 0, -1)
        return [Turn.from_json(r) for r in raw]

    async def reset_segment(self, session_id: str) -> list[Turn]:
        """
        Atomically read and clear the segment buffer.

        Used by the Segment Detector when a boundary fires (and by
        Session Wrap with force=true). Two concurrent callers are
        serialised by Valkey: the loser sees an empty list.
        """
        seg_key = segment_key(session_id)
        s_key = state_key(session_id)
        async with self._redis.pipeline(transaction=True) as p:
            p.lrange(seg_key, 0, -1)
            p.delete(seg_key)
            p.expire(s_key, self._ttl)
            results = await p.execute()
        raw_turns = results[0]
        return [Turn.from_json(r) for r in raw_turns]

    # --- State HASH --------------------------------------------------------

    async def get_state(self, session_id: str) -> WorkingMemoryState:
        """Read the full state hash and return a typed model."""
        raw = await self._redis.hgetall(state_key(session_id))
        if not raw:
            raise WorkingMemoryError(
                f"No working memory found for session {session_id!r}; "
                "did /session/start succeed?"
            )
        return parse_state_hash(raw)

    async def get_contributor_display_name(self, session_id: str) -> str:
        """Read the contributor's display name from working memory.

        Returns the stored value, or empty string when the field was
        never set (Node omitted ``contributor_display_name`` on
        ``/session/start``). Archive-side workers consume this for
        natural attribution; an empty string means fall back to neutral.
        """
        raw = await self._redis.hget(
            state_key(session_id), "contributor_display_name"
        )
        if raw is None:
            return ""
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    async def update_rolling_summary(
        self,
        session_id: str,
        new_summary: str,
    ) -> None:
        """
        Atomically promote rolling_summary -> prior_rolling_summary,
        then set rolling_summary to ``new_summary``.

        This is the entry point owned by the Segment Detector path
        (CLAUDE.md invariant #15). The summary is always a fresh
        compressed rewrite, never appended.
        """
        await self._redis.eval(
            _UPDATE_ROLLING_SUMMARY_LUA,
            1,
            state_key(session_id),
            new_summary,
            str(self._ttl),
        )

    async def increment_user_turns_since_segment_check(
        self,
        session_id: str,
    ) -> int:
        """Atomically increment the user-turn counter that gates the
        Segment Detector cadence. Returns the new value.
        """
        s_key = state_key(session_id)
        async with self._redis.pipeline(transaction=True) as p:
            p.hincrby(s_key, "signal_user_turns_since_segment_check", 1)
            p.expire(s_key, self._ttl)
            results = await p.execute()
        return int(results[0])

    async def reset_user_turns_since_segment_check(
        self,
        session_id: str,
    ) -> None:
        """Reset the user-turn cadence counter to 0. Called by the
        Segment Detector after every invocation, regardless of whether a
        boundary fired.
        """
        await self.update_signals(
            session_id,
            signal_user_turns_since_segment_check=0,
        )

    async def increment_segments_pushed(self, session_id: str) -> int:
        """Atomically increment the session's pushed-segment counter."""
        s_key = state_key(session_id)
        async with self._redis.pipeline(transaction=True) as p:
            p.hincrby(s_key, "segments_pushed_this_session", 1)
            p.expire(s_key, self._ttl)
            results = await p.execute()
        return int(results[0])

    async def update_signals(self, session_id: str, **signals: Any) -> None:
        """
        Partial update of state fields.

        Only the supplied keys are written. Values are coerced to str
        before HSET. Empty kwargs is a no-op (don't refresh TTL on a
        no-op call — a no-op call has no business extending the lease).
        """
        if not signals:
            return
        mapping = {k: str(v) for k, v in signals.items()}
        s_key = state_key(session_id)
        async with self._redis.pipeline(transaction=True) as p:
            p.hset(s_key, mapping=mapping)
            p.expire(s_key, self._ttl)
            await p.execute()

    async def set_seeded_question(
        self,
        session_id: str,
        question_id: str | None,
    ) -> None:
        """Set or clear the last seeded question id."""
        await self.update_signals(
            session_id,
            last_seeded_question_id=question_id or "",
        )

    async def append_asked_question(
        self,
        session_id: str,
        question_id: str,
    ) -> None:
        """Push a seeded question id and trim the recently asked window.

        The window is stored as a Valkey LIST at
        ``wm:session:{session_id}:asked``. It is session-scoped and read by
        the steady Phase Gate for duplicate avoidance and themes diversity.
        """
        from flashback.phase_gate.ranking import RECENTLY_ASKED_WINDOW

        key = asked_key(session_id)
        s_key = state_key(session_id)
        async with self._redis.pipeline(transaction=True) as p:
            p.rpush(key, question_id)
            p.ltrim(key, -RECENTLY_ASKED_WINDOW, -1)
            p.expire(key, self._ttl)
            p.expire(s_key, self._ttl)
            await p.execute()

    async def get_recently_asked_question_ids(self, session_id: str) -> list[str]:
        """Return the last 5 seeded question ids, oldest first."""
        raw = await self._redis.lrange(asked_key(session_id), 0, -1)
        return [
            item.decode("utf-8") if isinstance(item, bytes) else str(item)
            for item in raw
        ]

    async def record_tap_emitted(
        self,
        session_id: str,
        question_id: str,
        question_text: str = "",
    ) -> None:
        """Increment the session tap counter and keep a 5-item FIFO id list.

        Also resets ``user_turns_since_last_tap`` to 0 so the cooldown
        gate suppresses back-to-back taps, and stashes the question text
        in ``signal_pending_tap_question`` so the Intent Classifier on
        the next turn knows the user is answering a specific question
        (and shouldn't read a short answer as `switch`).
        """
        s_key = state_key(session_id)
        state = await self.get_state(session_id)
        emitted = [*state.emitted_tap_question_ids, question_id][-5:]
        async with self._redis.pipeline(transaction=True) as p:
            p.hincrby(s_key, "taps_emitted_this_session", 1)
            p.hset(s_key, "emitted_tap_question_ids", json.dumps(emitted))
            p.hset(s_key, "user_turns_since_last_tap", "0")
            p.hset(s_key, "signal_pending_tap_question", question_text)
            p.expire(s_key, self._ttl)
            await p.execute()

    async def increment_user_turns_since_last_tap(self, session_id: str) -> int:
        """Atomically increment the tap cooldown counter. Returns new value."""
        s_key = state_key(session_id)
        async with self._redis.pipeline(transaction=True) as p:
            p.hincrby(s_key, "user_turns_since_last_tap", 1)
            p.expire(s_key, self._ttl)
            results = await p.execute()
        return int(results[0])

    async def clear_pending_tap_question(self, session_id: str) -> None:
        """Clear the cached tap-question text after the classifier has read it.

        Keeps the signal scoped to the single turn that immediately
        follows the tap emission. Leaving it set across multiple turns
        would mis-classify later replies as tap-answers.
        """
        await self.update_signals(session_id, signal_pending_tap_question="")

    # --- Internal ----------------------------------------------------------

    async def _refresh_ttls(self, session_id: str) -> None:
        """Extend the lease on all three keys."""
        keys = all_keys(session_id)
        async with self._redis.pipeline(transaction=True) as p:
            for k in keys:
                p.expire(k, self._ttl)
            await p.execute()
