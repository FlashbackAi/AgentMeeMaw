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
    # Presigned URLs (Node-minted) for the Python-owned video render: a GET for
    # the prime photo and PUTs for the MP4 + PDF. The tribute_render worker
    # transfers through them (no S3 creds on our side). Required for
    # artifact_kind='tribute_video'; expiry must cover queue latency + render.
    video_put_url: str | None = None
    pdf_put_url: str | None = None
    # PUT for the cover poster (the opener page: portrait + title) as a JPEG.
    # Optional; when present the worker uploads it and Node writes thumbnail_url
    # so the tribute card/thumbnail shows the cover, not a stray video frame.
    poster_put_url: str | None = None
    prime_photo_get_url: str | None = None


class TributeGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    tribute_id: UUID
    artifact_kind: Literal["tribute_video", "storybook"]
    enqueued: bool
    percent: int
    ready: bool
    scene_count: int


class TributeRegenerateRequest(BaseModel):
    """Re-render a tribute video from the SAME stored assembly inputs.

    Reuses everything on the row's prior tribute_video context (candidates,
    message, leads, knobs) and only overlays fresh Node-minted presigned URLs
    + a new composed_at -- the old URLs have expired by the time a user taps
    regenerate. The worker re-assembles the Book from the same inputs, so the
    LLM produces a fresh take on the same data.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    video_put_url: str | None = None
    pdf_put_url: str | None = None
    poster_put_url: str | None = None
    prime_photo_get_url: str | None = None


class TributeEditRequest(BaseModel):
    """Re-render a tribute video with cumulative free-text adjustments.

    Like the moments /edit contract: Node owns the edit history and sends the
    full ``prior_instructions`` list each call; the agent applies
    ``prior_instructions + [instructions]`` as the family's edit requests. A
    tapped suggestion chip flows in as ``instructions`` text. Fresh presigned
    URLs are required (the prior render's URLs have expired), same as
    regenerate.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    instructions: str | None = None
    prior_instructions: list[str] = Field(default_factory=list)
    video_put_url: str | None = None
    pdf_put_url: str | None = None
    poster_put_url: str | None = None
    prime_photo_get_url: str | None = None


class TributeEditSuggestionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID


class TributeEditSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    instruction: str


class TributeEditSuggestionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[TributeEditSuggestion]


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


class TributeProgressSlotOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    hint: str
    filled: bool
    # Granular progress, populated for the memories slot only (else null).
    count: int | None = None
    target: int | None = None


class TributeProgressResponse(BaseModel):
    """Decorated tribute completion meter for GET /tributes/{id}/progress.

    Identical shape to the `tribute_progress` block on /turn metadata
    (both serialize through `progress_to_payload`), so the frontend can
    render the same meter whether it polls this endpoint or reads it off
    a turn. `title` is the campaign skin's display name; `next` is the key
    of the first unfilled slot (drives the "next -- ..." steer).
    """

    model_config = ConfigDict(extra="forbid")

    percent: int
    ready: bool
    title: str
    next: str | None = None
    slots: list[TributeProgressSlotOut]


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
    # Tribute live meter when the session is in a tribute flow, else None.
    # Shape: {percent, ready, title, next, slots:[{key, label, hint,
    # filled, count, target}]}. `title` is the campaign skin's display
    # name; `next` is the key of the first unfilled slot (drives the
    # "next -- ..." steer); per-slot `hint` is the actionable copy and
    # `count`/`target` give granular progress (memories slot only, else
    # null). The percent math lives in the tribute_status SQL view.
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
    # The contributor's own gender. The contributor is depicted alongside
    # the subject in some moment scenes ("my father and I on a bike"), so we
    # capture and persist it to keep generated figures gender-correct.
    contributor_gender: Literal["he", "she", "they"] | None = None
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
    contributor_gender: Literal["he", "she", "they"] | None = None
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
    # Upper bound tracks the live question set: every archetype now returns 10
    # questions (5 base + 3 universal + 2 ground-truth), and Node requires every
    # returned question answered exactly once. 12 leaves headroom (mirrors Node's
    # 3-12 guard). An 8-cap silently 422'd every full submission.
    answers: list[ArchetypeAnswerInput] = Field(min_length=3, max_length=12)
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


# --- /storybooks (Python render pipeline, spec 2026-06-29) ------------------


class StorybookCollectionInfo(BaseModel):
    """One row of ``GET /storybook-collections`` -- the chooser surface.

    ``page_count`` tells Node how many page PUT URLs to mint
    (cover + page_count pages + the PDF).
    """

    slug: str
    display_name: str
    layout: str  # "grid" | "chapter"
    page_count: int


class _StorybookRenderUrls(BaseModel):
    """The Node-minted presigned URLs every storybook render needs.

    ``anchor_photo_get_url`` follows the latest-profile-picture-context rule:
    minted from ``persons.latest_generation_context.reference_s3_key`` when
    its ``mode`` is ``with_reference``; omitted when ``no_reference``.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    pdf_put_url: str = Field(min_length=1)
    cover_put_url: str = Field(min_length=1)
    page_put_urls: list[str] = Field(min_length=1, max_length=32)
    anchor_photo_get_url: str | None = None


class StorybookGenerateRequest(_StorybookRenderUrls):
    """Body for ``POST /storybooks`` -- mint a new collection storybook.

    ``storybook_id`` is CALLER-SUPPLIED (Node generates it, like Phase-5
    session ids): the row doesn't exist when Node mints the presigned PUT
    URLs, and its completion listener re-derives the S3 keys from the id
    with no persistence -- so the id must be known at mint time.
    """

    storybook_id: UUID
    collection: str = Field(min_length=1, max_length=64)


class StorybookRegenerateRequest(_StorybookRenderUrls):
    """Body for ``POST /storybooks/{id}/regenerate`` -- redraw the art,
    keep the stored script."""


class StorybookEditRequest(_StorybookRenderUrls):
    """Body for ``POST /storybooks/{id}/edit`` -- re-assemble the script
    honouring cumulative edit requests, then re-render.

    Mirrors :class:`ArtifactEditRequest`: ``instructions`` is the newest edit,
    ``prior_instructions`` the cumulative history Node tracks in Dynamo.
    """

    instructions: str = Field(min_length=1, max_length=500)
    prior_instructions: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("instructions", mode="before")
    @classmethod
    def _strip_storybook_instructions(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("prior_instructions", mode="before")
    @classmethod
    def _strip_storybook_prior(cls, value):
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


class StorybookJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    storybook_id: UUID
    person_id: UUID
    collection: str
    status: Literal["generating"]
    source: Literal["manual", "regenerate", "edit"]
    moments_count: int
    enqueued: bool


# --- /health ---------------------------------------------------------------


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    checks: dict[str, str] = Field(default_factory=dict)
