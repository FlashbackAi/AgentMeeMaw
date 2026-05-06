"""
Pydantic models for Working Memory.

Two shapes:

* :class:`Turn` — a single assistant or user turn, stored as a JSON string
  inside the transcript and segment LISTs.
* :class:`WorkingMemoryState` — the typed view of the state HASH. The
  HASH stores everything as Valkey strings; this model handles
  string-to-typed conversion on read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["user", "assistant"]
EmotionalTemperature = Literal["low", "medium", "high"]


class Turn(BaseModel):
    """One turn — appended to both transcript and segment LISTs."""

    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def _ensure_tz(cls, v: datetime) -> datetime:
        """Force UTC. Naive datetimes get tagged with UTC; aware ones are
        converted. Storing tz-naive ISO strings produces ambiguity later
        when the rolling-summary regenerator reads them back."""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> "Turn":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return cls.model_validate_json(raw)


class WorkingMemoryState(BaseModel):
    """
    Typed view of the state HASH.

    All HASH values are Valkey strings on the wire; this model handles
    parsing on read and serialisation on write. ``signal_*`` fields are
    namespaced so the Segment Detector and Intent Classifier can update
    them without colliding with identity / opener fields.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: str
    role_id: str
    started_at: datetime

    rolling_summary: str = ""
    prior_rolling_summary: str = ""
    prior_session_summary: str = ""
    segments_pushed_this_session: int = 0

    signal_user_turns_since_segment_check: int = 0
    signal_recent_words: str = ""
    signal_last_user_message_length: int = 0
    signal_emotional_temperature_estimate: str = ""
    signal_last_intent: str = ""

    last_opener: str = ""
    last_seeded_question_id: str = ""

    @field_validator("started_at")
    @classmethod
    def _ensure_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


# The set of int-typed signal fields. Used by the client when reading
# the HASH back, since Valkey returns everything as strings.
_INT_FIELDS: frozenset[str] = frozenset(
    {
        "signal_user_turns_since_segment_check",
        "signal_last_user_message_length",
        "segments_pushed_this_session",
    }
)


def parse_state_hash(raw: dict[str, str | bytes]) -> WorkingMemoryState:
    """
    Build a WorkingMemoryState from the raw HGETALL result.

    Bytes-or-str keys are tolerated; everything is normalised to str
    before the pydantic model validates. Missing fields fall back to
    pydantic defaults.
    """

    def _to_str(v: str | bytes) -> str:
        return v.decode("utf-8") if isinstance(v, bytes) else v

    parsed: dict[str, object] = {}
    for k, v in raw.items():
        key = _to_str(k)
        value = _to_str(v)
        if key == "started_at":
            parsed[key] = datetime.fromisoformat(value)
        elif key in _INT_FIELDS:
            parsed[key] = int(value)
        else:
            parsed[key] = value
    return WorkingMemoryState.model_validate(parsed)


def serialise_state_for_init(state: WorkingMemoryState) -> dict[str, str]:
    """Render a state model into the str-only mapping HSET expects."""
    return {
        "person_id": state.person_id,
        "role_id": state.role_id,
        "started_at": state.started_at.isoformat(),
        "rolling_summary": state.rolling_summary,
        "prior_rolling_summary": state.prior_rolling_summary,
        "prior_session_summary": state.prior_session_summary,
        "segments_pushed_this_session": str(state.segments_pushed_this_session),
        "signal_user_turns_since_segment_check": str(state.signal_user_turns_since_segment_check),
        "signal_recent_words": state.signal_recent_words,
        "signal_last_user_message_length": str(state.signal_last_user_message_length),
        "signal_emotional_temperature_estimate": state.signal_emotional_temperature_estimate,
        "signal_last_intent": state.signal_last_intent,
        "last_opener": state.last_opener,
        "last_seeded_question_id": state.last_seeded_question_id,
    }
