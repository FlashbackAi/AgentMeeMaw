"""Pydantic request and response models for the HTTP surface.

Mirrors the contract in CLAUDE.md s8 and the step-4 prompt's API
section. Uses pydantic v2 syntax (``model_config = ConfigDict(...)``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flashback.orchestrator.protocol import Tap

# Conversation mode. ``text`` is the default chat surface; ``voice`` is
# the Gemini STT -> agent -> Gemini TTS flow proxied through Node, in which
# the reply is spoken aloud by TTS. In voice mode the response generator
# drops markdown, leans on a conversational register, and prefixes the
# reply with a single style tag that this service lifts into
# ``metadata.voice_style`` for Node to map to a Gemini TTS style.
Mode = Literal["text", "voice"]


class QuestionChipsOut(BaseModel):
    """Chip metadata for a seeded producer-bank question.

    Rendered as Skip / Don't ask again / I'll tell you later beneath
    the bot reply. The Node UI POSTs a chosen action back on the next
    `/turn` via :class:`QuestionDecisionInput`.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: UUID
    actions: list[Literal["skip", "suppress", "defer"]]


class QuestionDecisionInput(BaseModel):
    """User decision on a producer-bank question carried on the next /turn."""

    model_config = ConfigDict(extra="forbid")

    question_id: UUID
    action: Literal["skip", "suppress", "defer"]


class GroundTruthAnswerInput(BaseModel):
    """Structured answer to a ground-truth / segment-anchor tap, carried
    on the next /turn. The conversation text never carries this Q&A —
    extraction never mines it (design 2026-06-11 §3c)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ground_truth", "segment_anchor"]
    field: str | None = Field(default=None, max_length=64)
    option_label: str | None = Field(default=None, max_length=200)
    free_text: str | None = Field(default=None, max_length=500)
    skipped: bool = False


class MessageAnswerInput(BaseModel):
    """Structured answer to a tribute message-invitation tap, carried on
    the next /turn. Never enters the transcript — extraction never mines
    it (design 2026-06-14 §5). The free text is polished into
    ``tributes.message_text``."""

    model_config = ConfigDict(extra="forbid")

    option_label: str | None = Field(default=None, max_length=200)
    free_text: str | None = Field(default=None, max_length=2000)
    skipped: bool = False


class TributeGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    artifact_kind: Literal["tribute_video", "storybook"] = "tribute_video"
    preset: str | None = None
    campaign: str | None = None
    # S3 key of the contributor-uploaded prime/profile photo for the FD
    # storybook cover (Node-owned upload). The agent only passes the key into
    # latest_generation_context; Node renders the cover image-to-image.
    prime_photo_s3_key: str | None = None
    # True when prime_photo_s3_key is already a prime-years photo (skip de-age);
    # False (default) when it's a current/older/profile photo that should be
    # de-aged to his prime years. Node sets this based on which photo it sent.
    cover_photo_is_prime_years: bool = False


class TributeGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    tribute_id: UUID
    artifact_kind: Literal["tribute_video", "storybook"]
    enqueued: bool
    percent: int
    ready: bool
    scene_count: int


class TributeCampaignOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    featured: bool
    is_active: bool
    active_start: str | None = None
    active_end: str | None = None


class TributeCampaignsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaigns: list[TributeCampaignOut]
    active_featured_slug: str | None = None


# --- /session/start --------------------------------------------------------


class SessionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    person_id: UUID
    role_id: UUID
    contributor_display_name: str | None = None
    session_metadata: dict = Field(default_factory=dict)
    mode: Mode = "text"


class SessionStartMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["starter", "steady"]
    selected_question_id: UUID | None = None
    taps: list[Tap] = Field(default_factory=list)
    question_chips: QuestionChipsOut | None = None
    # Voice mode only: prosody label for the opener; Node maps it to a
    # Gemini TTS style. None in text mode.
    voice_style: str | None = None


class SessionStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    opener: str
    metadata: SessionStartMetadata


# --- /turn -----------------------------------------------------------------


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    person_id: UUID
    role_id: UUID
    message: str = Field(min_length=1, max_length=8000)
    question_decision: QuestionDecisionInput | None = None
    ground_truth_answer: GroundTruthAnswerInput | None = None
    message_answer: MessageAnswerInput | None = None
    mode: Mode = "text"


class TurnMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str | None = None
    emotional_temperature: Literal["low", "medium", "high"] | None = None
    segment_boundary: bool = False
    taps: list[Tap] = Field(default_factory=list)
    question_chips: QuestionChipsOut | None = None
    # Voice mode only: prosody label for the reply; Node maps it to a
    # Gemini TTS style. None in text mode.
    voice_style: str | None = None
    # Tribute live meter: {percent, ready, slots:[...]} when the session is
    # in a tribute flow, else None.
    tribute_progress: dict | None = None


class TurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    metadata: TurnMetadata


# --- /session/wrap ---------------------------------------------------------


class SessionWrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    person_id: UUID


class SessionWrapMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments_extracted_count: int = 0


class SessionWrapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_summary: str
    metadata: SessionWrapMetadata


# --- /admin/reset_phase ----------------------------------------------------


class ResetPhaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID


class ResetPhaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    previous_phase: Literal["starter", "steady"]
    previous_locked_at: str | None = None


# --- /persons --------------------------------------------------------------


class PersonCreateRequest(BaseModel):
    """Body for ``POST /persons``.

    Node calls this once during onboarding, after the contributor has
    supplied the subject's display name, their own relationship to
    them, and their contributor display name. DOB / DOD are deliberately
    not accepted (CLAUDE.md s1).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    relationship: str = Field(min_length=1, max_length=80)
    contributor_display_name: str = Field(min_length=1, max_length=64)
    gender: Literal["he", "she", "they"] | None = None
    # Onboarding reference photo of the subject. Node uploads it to S3 and
    # passes the key here; onboarding carries no instruction text — the
    # reference alone anchors the likeness while the prompt applies our
    # painterly RDR2 style. Stored on ``persons.latest_generation_context``
    # exactly like the regenerate/edit reference path. Omit / null for the
    # text-only no-reference flow. (Text + reference happens later, on
    # regenerate — see :class:`ProfilePictureGenerateRequest`.)
    reference_s3_key: str | None = Field(default=None, max_length=500)

    @field_validator(
        "name",
        "relationship",
        "contributor_display_name",
        mode="before",
    )
    @classmethod
    def _strip(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class PersonCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    name: str
    relationship: str
    gender: Literal["he", "she", "they"] | None = None
    phase: Literal["starter", "steady"]
    created_at: datetime


# --- /api/v1/onboarding ----------------------------------------------------


class ArchetypeAnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=120)
    option_id: str | None = Field(default=None, max_length=120)
    free_text: str | None = Field(default=None, max_length=500)
    skipped: bool = False

    @field_validator("question_id", "option_id", "free_text", mode="before")
    @classmethod
    def _strip_optional(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class ArchetypeAnswersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    answers: list[ArchetypeAnswerInput] = Field(min_length=3, max_length=8)
    contributor_display_name: str | None = Field(default=None, max_length=64)

    @field_validator("contributor_display_name", mode="before")
    @classmethod
    def _strip_contributor_name(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class ArchetypeAnswersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    opener: str


class ArchetypeQuestionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    relationship: str | None = None
    archetype: str
    questions: list[dict[str, Any]]


# --- /persons/{person_id}/profile-picture ---------------------------------


class ProfilePictureGenerateRequest(BaseModel):
    """Body for ``POST /persons/{person_id}/profile-picture``.

    The post-onboarding regenerate surface. Node sends this when the user
    re-rolls the portrait: optionally a fresh ``reference_s3_key`` (uploaded
    photo to anchor the likeness), optionally a free-text ``instructions``
    note alongside it (e.g. "make him look like this"), and optionally a
    stylistic ``preset``. Any combination is valid — all three are optional.

    ``preset`` is a slug from the shared artifact-preset registry. ``None``
    (or omitted) uses the default RDR2 painterly-cinematic look. Unlike the
    ``/edit`` endpoint there is no instruction stacking here — this is a
    single one-shot note appended to the portrait prompt.
    """

    model_config = ConfigDict(extra="forbid")

    reference_s3_key: str | None = Field(default=None, max_length=500)
    instructions: str | None = Field(default=None, max_length=500)
    preset: str | None = Field(default=None, max_length=64)

    @field_validator("instructions", mode="before")
    @classmethod
    def _strip_instructions(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class ProfilePictureEditRequest(BaseModel):
    """Body for ``POST /persons/{person_id}/profile-picture/edit``.

    ``instructions`` is the newest edit text. ``prior_instructions`` is the
    cumulative history Node tracks in Dynamo across earlier edits, oldest
    first. The agent composes them in order so the final prompt carries
    every accepted edit (e.g. ``["he has glasses", "and a Rolls Royce"]``
    + ``instructions="wearing a brown coat"`` stacks all three).

    ``reference_s3_key`` is optional. Pass the prior generated image's S3
    key to chain refinement; omit (or pass null) to start from text only.
    Node decides which reference to send.
    """

    model_config = ConfigDict(extra="forbid")

    instructions: str = Field(min_length=1, max_length=500)
    prior_instructions: list[str] = Field(default_factory=list, max_length=50)
    reference_s3_key: str | None = Field(default=None, max_length=500)
    preset: str | None = Field(default=None, max_length=64)

    @field_validator("instructions", mode="before")
    @classmethod
    def _strip(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("prior_instructions", mode="before")
    @classmethod
    def _strip_prior(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        out: list[str] = []
        for entry in value:
            if isinstance(entry, str):
                stripped = entry.strip()
                if stripped:
                    out.append(stripped)
        return out


class ProfilePictureJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    person_id: UUID
    mode: Literal["no_reference", "with_reference"]
    source: Literal["onboarding", "regenerate", "edit"]
    preset: str
    enqueued: bool


# --- /artifact-presets, /artifacts -----------------------------------------


class ArtifactPresetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    label: str
    description: str
    is_default: bool


class ArtifactPresetsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presets: list[ArtifactPresetOut]


# ``record_type`` values that route through the generic artifact-edit
# surface. ``person`` is excluded — profile-picture has its own endpoint.
ArtifactRecordType = Literal["moment", "entity", "thread"]


class ArtifactRegenerateRequest(BaseModel):
    """Body for ``POST /artifacts/{record_type}/{record_id}/regenerate``.

    Node calls this when the user clicks "regenerate" on a moment / entity
    / thread artifact and optionally picks a preset or uploads a reference
    image. The agent re-composes the prompt from the row's stored
    ``generation_prompt`` + the chosen preset and enqueues a fresh job on
    ``artifact_generation``.

    ``reference_s3_key`` is allowed for ``moment`` and ``entity`` only —
    threads reject it with 400 because they're abstract arcs that don't
    benefit from a visual anchor.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    preset: str | None = Field(default=None, max_length=64)
    reference_s3_key: str | None = Field(default=None, max_length=500)


class ArtifactEditRequest(BaseModel):
    """Body for ``POST /artifacts/{record_type}/{record_id}/edit``.

    Mirrors :class:`ProfilePictureEditRequest` for moment / entity / thread
    artifacts. ``instructions`` is the newest user edit; ``prior_instructions``
    is the cumulative history Node tracks in Dynamo so the composed prompt
    carries every accepted edit in order.

    Same reference-image rule as regenerate: moments + entities accept it,
    threads reject it with 400.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    instructions: str = Field(min_length=1, max_length=500)
    prior_instructions: list[str] = Field(default_factory=list, max_length=50)
    reference_s3_key: str | None = Field(default=None, max_length=500)
    preset: str | None = Field(default=None, max_length=64)

    @field_validator("instructions", mode="before")
    @classmethod
    def _strip_artifact_instructions(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("prior_instructions", mode="before")
    @classmethod
    def _strip_artifact_prior(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        out: list[str] = []
        for entry in value:
            if isinstance(entry, str):
                stripped = entry.strip()
                if stripped:
                    out.append(stripped)
        return out


class ArtifactJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    record_type: ArtifactRecordType
    record_id: UUID
    person_id: UUID
    artifact_kind: Literal["image", "video"]
    mode: Literal["no_reference", "with_reference"]
    source: Literal["regenerate", "edit"]
    preset: str
    enqueued: bool


# --- /health ---------------------------------------------------------------


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    checks: dict[str, str] = Field(default_factory=dict)
