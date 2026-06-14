"""Protocol and result shapes consumed by the HTTP layer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

Mode = Literal["text", "voice"]

from pydantic import BaseModel, ConfigDict, Field


class Tap(BaseModel):
    """A tappable question chip surfaced beneath an agent reply.

    `options` are short tappable answer chips generated per-turn by a
    small LLM call. Empty list when generation failed or was skipped —
    the UI falls back to free-text input only.

    `kind` distinguishes the tap surfaces: `coverage` (P0 bank, has a
    question row), `ground_truth` (registry field capture — no question
    row, `field` carries the registry key), `segment_anchor` (time anchor
    for the live story), and `message` (tribute message invitation — no
    question row; the answer returns as the `message_answer` sidecar and
    is polished into `tributes.message_text`). Ground-truth, anchor, and
    message answers return as structured sidecars on the next /turn,
    never as mined text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: UUID | None
    text: str
    dimension: str
    options: list[str] = Field(default_factory=list)
    kind: Literal["coverage", "ground_truth", "segment_anchor", "message"] = "coverage"
    field: str | None = None


class QuestionChips(BaseModel):
    """Skip / Don't ask again / I'll tell you later — chip surface for
    producer-bank questions.

    Rendered inline below the bot reply. Distinct from :class:`Tap`,
    which is the P0 coverage-tap surface (those carry answer-option
    chips). These chips capture *meta-intent* about whether to be
    asked again, not a substantive answer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: UUID
    actions: list[Literal["skip", "suppress", "defer"]] = Field(
        default_factory=lambda: ["skip", "suppress", "defer"]
    )


@dataclass(frozen=True)
class SessionStartResult:
    opener: str
    phase: str
    selected_question_id: UUID | None
    taps: list[Tap]
    chips: QuestionChips | None = None
    # Voice-mode prosody label for the opener (None in text mode).
    voice_style: str | None = None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    intent: str | None
    emotional_temperature: str | None
    segment_boundary: bool
    taps: list[Tap]
    chips: QuestionChips | None = None
    # Voice-mode prosody label for the reply (None in text mode).
    voice_style: str | None = None
    # Tribute live meter: {percent, ready, slots:[{key,label,filled}]} when
    # the session is in a tribute flow, else None.
    tribute_progress: dict | None = None


@dataclass(frozen=True)
class SessionWrapResult:
    session_summary: str
    segments_extracted_count: int


@dataclass(frozen=True)
class StreamEvent:
    """One event in the SSE stream emitted by /turn/stream and
    /session/start/stream.

    ``type`` becomes the SSE ``event:`` name; ``data`` is JSON-serialized
    as the ``data:`` payload. Event types:

      - ``meta``: pre-LLM metadata available before generation starts
        (intent, taps, chips for /turn; phase, taps, chips for
        /session/start).
      - ``voice_style``: voice mode only. ``data`` is ``{"style": "..."}``
        — the prosody label lifted from the reply's leading ``[[style:
        x]]`` tag. Emitted once, before the first ``text_delta``, so Node
        can set the Gemini TTS style. Never emitted in text mode.
      - ``text_delta``: a chunk of assistant text. ``data`` is
        ``{"text": "..."}``.
      - ``done``: stream finished cleanly. ``data`` carries the full
        reply text and post-LLM bits like ``segment_boundary`` (and
        ``voice_style`` in voice mode).
      - ``error``: terminal failure. ``data`` carries ``code``,
        ``message``, and ``partial_text`` (whatever streamed so far).
        No further events follow.
    """

    type: Literal["meta", "voice_style", "text_delta", "done", "error"]
    data: dict


class OrchestratorProtocol(Protocol):
    """The interface the HTTP routes consume."""

    async def handle_session_start(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        session_metadata: dict,
        mode: Mode = "text",
    ) -> SessionStartResult: ...

    async def handle_first_time_opener(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        session_metadata: dict,
        mode: Mode = "text",
    ) -> SessionStartResult: ...

    async def handle_turn(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        user_message: str,
        mode: Mode = "text",
    ) -> TurnResult: ...

    async def handle_session_wrap(
        self,
        session_id: UUID,
        person_id: UUID,
    ) -> SessionWrapResult: ...

    def handle_session_start_stream(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        session_metadata: dict,
        mode: Mode = "text",
    ) -> AsyncIterator["StreamEvent"]: ...

    def handle_first_time_opener_stream(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        session_metadata: dict,
        mode: Mode = "text",
    ) -> AsyncIterator["StreamEvent"]: ...

    def handle_turn_stream(
        self,
        session_id: UUID,
        person_id: UUID,
        role_id: UUID,
        user_message: str,
        mode: Mode = "text",
    ) -> AsyncIterator["StreamEvent"]: ...
