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
from typing import Any, Literal

from redis.asyncio import Redis

from flashback.working_memory.keys import (
    all_keys,
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
        seed_rolling_summary: str = "",
    ) -> None:
        """
        Create WM for a new session.

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
            rolling_summary=seed_rolling_summary,
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
    ) -> None:
        """
        Append to BOTH transcript and segment buffer.

        Trim transcript to the last ``transcript_limit`` entries. The
        segment buffer is *not* trimmed; it grows until a boundary
        fires, at which point :meth:`reset_segment` clears it.
        """
        turn = Turn(role=role, content=content, timestamp=timestamp)
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

    # --- Internal ----------------------------------------------------------

    async def _refresh_ttls(self, session_id: str) -> None:
        """Extend the lease on all three keys."""
        keys = all_keys(session_id)
        async with self._redis.pipeline(transaction=True) as p:
            for k in keys:
                p.expire(k, self._ttl)
            await p.execute()
