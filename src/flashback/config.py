"""
Environment-driven configuration for the agent service.

Every tunable knob - DB URL, queue URL, embedding model identity,
AWS region - is read from environment variables. There are no
config files. Tests construct ``Config`` directly with overrides.

The embedding model identity (``EMBEDDING_MODEL`` +
``EMBEDDING_MODEL_VERSION``) is stamped onto every row alongside
the vector. Changing either value in the environment is the
documented way to roll forward to a new model snapshot - the
version-guarded UPDATE in the worker uses the (model, version) pair
as the identity that must match.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from flashback.env import load_dotenv_local

load_dotenv_local()


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise ConfigError(
            f"Required environment variable {name!r} is not set. "
            f"See .env.example for the full list."
        )
    return value


def _env_bool(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    database_url: str
    embedding_queue_url: str
    voyage_api_key: str
    embedding_model: str
    embedding_model_version: str
    aws_region: str

    sqs_max_messages: int
    sqs_wait_seconds: int
    db_pool_min_size: int
    db_pool_max_size: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            database_url=_required("DATABASE_URL"),
            embedding_queue_url=_required("EMBEDDING_QUEUE_URL"),
            voyage_api_key=_required("VOYAGE_API_KEY"),
            embedding_model=os.environ.get("EMBEDDING_MODEL", "voyage-3-large"),
            embedding_model_version=os.environ.get(
                "EMBEDDING_MODEL_VERSION", "2025-01-07"
            ),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            sqs_max_messages=int(os.environ.get("SQS_MAX_MESSAGES", "10")),
            sqs_wait_seconds=int(os.environ.get("SQS_WAIT_SECONDS", "20")),
            db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "1")),
            db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "4")),
        )


@dataclass(frozen=True)
class ExtractionConfig:
    """
    Configuration for the Extraction Worker (step 11).

    Like ``Config`` for the embedding worker, this is its own dataclass so
    the worker process loads only what it needs. The HTTP service does not
    use it; it has its own ``HttpConfig``.

    Notable inheritances (kept aligned with HttpConfig):

    * ``LLM_EXTRACTION_*`` defaults to the ``LLM_BIG_*`` family (Sonnet).
    * ``LLM_COMPATIBILITY_*`` defaults to the ``LLM_SMALL_*`` family
      (gpt-5.1).
    * ``EMBEDDING_MODEL`` and ``EMBEDDING_MODEL_VERSION`` are the same
      identity stamps the embedding worker uses; the extraction worker
      pushes embedding jobs with these values.
    """

    database_url: str
    aws_region: str

    extraction_queue_url: str
    embedding_queue_url: str
    artifact_queue_url: str
    thread_detector_queue_url: str

    voyage_api_key: str
    embedding_model: str
    embedding_model_version: str

    openai_api_key: str
    anthropic_api_key: str

    llm_extraction_provider: str
    llm_extraction_model: str
    llm_extraction_timeout_seconds: float
    llm_extraction_max_tokens: int

    llm_compatibility_provider: str
    llm_compatibility_model: str
    llm_compatibility_timeout_seconds: float
    llm_compatibility_max_tokens: int

    extraction_refinement_distance_threshold: float
    extraction_refinement_candidate_limit: int
    extraction_voyage_query_timeout_seconds: float

    sqs_wait_seconds: int
    db_pool_min_size: int
    db_pool_max_size: int

    @classmethod
    def from_env(cls) -> "ExtractionConfig":
        small_provider = os.environ.get("LLM_SMALL_PROVIDER", "openai")
        small_model = os.environ.get("LLM_SMALL_MODEL", "gpt-5.1")
        big_provider = os.environ.get("LLM_BIG_PROVIDER", "anthropic")
        big_model = os.environ.get("LLM_BIG_MODEL", "claude-sonnet-4-6")
        return cls(
            database_url=_required("DATABASE_URL"),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            extraction_queue_url=_required("EXTRACTION_QUEUE_URL"),
            embedding_queue_url=_required("EMBEDDING_QUEUE_URL"),
            artifact_queue_url=_required("ARTIFACT_QUEUE_URL"),
            thread_detector_queue_url=_required("THREAD_DETECTOR_QUEUE_URL"),
            voyage_api_key=_required("VOYAGE_API_KEY"),
            embedding_model=os.environ.get("EMBEDDING_MODEL", "voyage-3-large"),
            embedding_model_version=os.environ.get(
                "EMBEDDING_MODEL_VERSION", "2025-01-07"
            ),
            openai_api_key=_required("OPENAI_API_KEY"),
            anthropic_api_key=_required("ANTHROPIC_API_KEY"),
            llm_extraction_provider=os.environ.get(
                "LLM_EXTRACTION_PROVIDER", big_provider
            ),
            llm_extraction_model=os.environ.get(
                "LLM_EXTRACTION_MODEL", big_model
            ),
            llm_extraction_timeout_seconds=float(
                os.environ.get("LLM_EXTRACTION_TIMEOUT_SECONDS", "45")
            ),
            llm_extraction_max_tokens=int(
                os.environ.get("LLM_EXTRACTION_MAX_TOKENS", "4000")
            ),
            llm_compatibility_provider=os.environ.get(
                "LLM_COMPATIBILITY_PROVIDER", small_provider
            ),
            llm_compatibility_model=os.environ.get(
                "LLM_COMPATIBILITY_MODEL", small_model
            ),
            llm_compatibility_timeout_seconds=float(
                os.environ.get("LLM_COMPATIBILITY_TIMEOUT_SECONDS", "8")
            ),
            llm_compatibility_max_tokens=int(
                os.environ.get("LLM_COMPATIBILITY_MAX_TOKENS", "800")
            ),
            extraction_refinement_distance_threshold=float(
                os.environ.get("EXTRACTION_REFINEMENT_DISTANCE_THRESHOLD", "0.35")
            ),
            extraction_refinement_candidate_limit=int(
                os.environ.get("EXTRACTION_REFINEMENT_CANDIDATE_LIMIT", "3")
            ),
            extraction_voyage_query_timeout_seconds=float(
                os.environ.get("EXTRACTION_VOYAGE_QUERY_TIMEOUT_SECONDS", "5")
            ),
            sqs_wait_seconds=int(os.environ.get("SQS_WAIT_SECONDS", "20")),
            db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "1")),
            db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "4")),
        )


@dataclass(frozen=True)
class HttpConfig:
    """
    Configuration for the FastAPI agent service (step 4).

    Kept separate from :class:`Config` (the embedding worker config) so
    each process loads only what it needs. ``VOYAGE_API_KEY`` is used
    by the retrieval query-embedder. ``EMBEDDING_QUEUE_URL`` is used by
    ``POST /profile_facts/upsert`` to push the re-embed job after a
    contributor edits a fact.
    """

    database_url: str
    valkey_url: str
    service_token: str
    http_host: str
    http_port: int
    working_memory_ttl_seconds: int
    working_memory_transcript_limit: int
    db_pool_min_size: int
    db_pool_max_size: int
    service_token_auth_disabled: bool = False
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    llm_small_provider: str = "openai"
    llm_small_model: str = "gpt-5.1"
    llm_big_provider: str = "anthropic"
    llm_big_model: str = "claude-sonnet-4-6"
    llm_intent_model: str = "gpt-5.1"
    llm_intent_timeout_seconds: float = 8.0
    llm_intent_max_tokens: int = 800
    llm_segment_detector_provider: str = "openai"
    llm_segment_detector_model: str = "gpt-5.1"
    llm_segment_detector_timeout_seconds: float = 10.0
    llm_segment_detector_max_tokens: int = 1000
    segment_detector_user_turn_cadence: int = 6
    llm_response_provider: str = "anthropic"
    llm_response_model: str = "claude-sonnet-4-6"
    llm_response_timeout_seconds: float = 12.0
    llm_response_max_tokens: int = 400
    extraction_queue_url: str = ""
    trait_synthesizer_queue_url: str = ""
    profile_summary_queue_url: str = ""
    producers_per_session_queue_url: str = ""
    embedding_queue_url: str = ""
    llm_session_summary_provider: str = "anthropic"
    llm_session_summary_model: str = "claude-sonnet-4-6"
    llm_session_summary_timeout_seconds: float = 12.0
    llm_session_summary_max_tokens: int = 300
    aws_region: str = "us-east-1"
    voyage_api_key: str = ""
    embedding_model: str = "voyage-3-large"
    embedding_model_version: str = "2025-01-07"
    retrieval_query_embed_timeout_seconds: float = 2.0
    retrieval_default_limit: int = 10
    retrieval_max_limit: int = 50

    @classmethod
    def from_env(cls) -> "HttpConfig":
        llm_small_model = os.environ.get("LLM_SMALL_MODEL", "gpt-5.1")
        llm_big_model = os.environ.get("LLM_BIG_MODEL", "claude-sonnet-4-6")
        return cls(
            database_url=_required("DATABASE_URL"),
            valkey_url=_required("VALKEY_URL"),
            service_token=_required("SERVICE_TOKEN"),
            service_token_auth_disabled=_env_bool("SERVICE_TOKEN_AUTH_DISABLED"),
            http_host=os.environ.get("HTTP_HOST", "0.0.0.0"),
            http_port=int(os.environ.get("HTTP_PORT", "8000")),
            working_memory_ttl_seconds=int(
                os.environ.get("WORKING_MEMORY_TTL_SECONDS", "86400")
            ),
            working_memory_transcript_limit=int(
                os.environ.get("WORKING_MEMORY_TRANSCRIPT_LIMIT", "30")
            ),
            db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "1")),
            db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "4")),
            openai_api_key=_required("OPENAI_API_KEY"),
            anthropic_api_key=_required("ANTHROPIC_API_KEY"),
            llm_small_provider=os.environ.get("LLM_SMALL_PROVIDER", "openai"),
            llm_small_model=llm_small_model,
            llm_big_provider=os.environ.get("LLM_BIG_PROVIDER", "anthropic"),
            llm_big_model=llm_big_model,
            llm_intent_model=os.environ.get("LLM_INTENT_MODEL", llm_small_model),
            llm_intent_timeout_seconds=float(
                os.environ.get("LLM_INTENT_TIMEOUT_SECONDS", "8")
            ),
            llm_intent_max_tokens=int(os.environ.get("LLM_INTENT_MAX_TOKENS", "800")),
            llm_segment_detector_provider=os.environ.get(
                "LLM_SEGMENT_DETECTOR_PROVIDER",
                os.environ.get("LLM_SMALL_PROVIDER", "openai"),
            ),
            llm_segment_detector_model=os.environ.get(
                "LLM_SEGMENT_DETECTOR_MODEL",
                llm_small_model,
            ),
            llm_segment_detector_timeout_seconds=float(
                os.environ.get("LLM_SEGMENT_DETECTOR_TIMEOUT_SECONDS", "10")
            ),
            llm_segment_detector_max_tokens=int(
                os.environ.get("LLM_SEGMENT_DETECTOR_MAX_TOKENS", "1000")
            ),
            segment_detector_user_turn_cadence=int(
                os.environ.get("SEGMENT_DETECTOR_USER_TURN_CADENCE", "6")
            ),
            llm_response_provider=os.environ.get("LLM_RESPONSE_PROVIDER", "anthropic"),
            llm_response_model=os.environ.get("LLM_RESPONSE_MODEL", llm_big_model),
            llm_response_timeout_seconds=float(
                os.environ.get("LLM_RESPONSE_TIMEOUT_SECONDS", "12")
            ),
            llm_response_max_tokens=int(
                os.environ.get("LLM_RESPONSE_MAX_TOKENS", "400")
            ),
            extraction_queue_url=_required("EXTRACTION_QUEUE_URL"),
            trait_synthesizer_queue_url=_required("TRAIT_SYNTHESIZER_QUEUE_URL"),
            profile_summary_queue_url=_required("PROFILE_SUMMARY_QUEUE_URL"),
            producers_per_session_queue_url=_required(
                "PRODUCERS_PER_SESSION_QUEUE_URL"
            ),
            embedding_queue_url=_required("EMBEDDING_QUEUE_URL"),
            llm_session_summary_provider=os.environ.get(
                "LLM_SESSION_SUMMARY_PROVIDER",
                os.environ.get("LLM_BIG_PROVIDER", "anthropic"),
            ),
            llm_session_summary_model=os.environ.get(
                "LLM_SESSION_SUMMARY_MODEL",
                llm_big_model,
            ),
            llm_session_summary_timeout_seconds=float(
                os.environ.get("LLM_SESSION_SUMMARY_TIMEOUT_SECONDS", "12")
            ),
            llm_session_summary_max_tokens=int(
                os.environ.get("LLM_SESSION_SUMMARY_MAX_TOKENS", "300")
            ),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            voyage_api_key=_required("VOYAGE_API_KEY"),
            embedding_model=os.environ.get("EMBEDDING_MODEL", "voyage-3-large"),
            embedding_model_version=os.environ.get(
                "EMBEDDING_MODEL_VERSION", "2025-01-07"
            ),
            retrieval_query_embed_timeout_seconds=float(
                os.environ.get("RETRIEVAL_QUERY_EMBED_TIMEOUT_SECONDS", "2")
            ),
            retrieval_default_limit=int(
                os.environ.get("RETRIEVAL_DEFAULT_LIMIT", "10")
            ),
            retrieval_max_limit=int(os.environ.get("RETRIEVAL_MAX_LIMIT", "50")),
        )


@dataclass(frozen=True)
class TraitSynthesizerConfig:
    """
    Configuration for the Trait Synthesizer worker (step 13).

    Sibling to :class:`ThreadDetectorConfig`. Drains the
    ``trait_synthesizer`` SQS queue (one message per person; producer
    is Session Wrap, step 16) and runs a single small-LLM call per
    person to upgrade/downgrade existing traits and propose new ones.

    A separate ``run-once --person-id <uuid>`` CLI path uses the same
    synthesizer logic synchronously without the queue. For that path
    ``trait_synthesizer_queue_url`` may be empty.
    """

    database_url: str
    aws_region: str

    trait_synthesizer_queue_url: str
    embedding_queue_url: str

    embedding_model: str
    embedding_model_version: str

    openai_api_key: str
    anthropic_api_key: str

    llm_trait_synth_provider: str
    llm_trait_synth_model: str
    llm_trait_synth_timeout_seconds: float
    llm_trait_synth_max_tokens: int

    sqs_wait_seconds: int
    db_pool_min_size: int
    db_pool_max_size: int

    @classmethod
    def from_env(cls, *, queue_required: bool = True) -> "TraitSynthesizerConfig":
        small_provider = os.environ.get("LLM_SMALL_PROVIDER", "openai")
        small_model = os.environ.get("LLM_SMALL_MODEL", "gpt-5.1")
        queue_url = (
            _required("TRAIT_SYNTHESIZER_QUEUE_URL")
            if queue_required
            else os.environ.get("TRAIT_SYNTHESIZER_QUEUE_URL", "")
        )
        return cls(
            database_url=_required("DATABASE_URL"),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            trait_synthesizer_queue_url=queue_url,
            embedding_queue_url=_required("EMBEDDING_QUEUE_URL"),
            embedding_model=os.environ.get("EMBEDDING_MODEL", "voyage-3-large"),
            embedding_model_version=os.environ.get(
                "EMBEDDING_MODEL_VERSION", "2025-01-07"
            ),
            openai_api_key=_required("OPENAI_API_KEY"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            llm_trait_synth_provider=os.environ.get(
                "LLM_TRAIT_SYNTH_PROVIDER", small_provider
            ),
            llm_trait_synth_model=os.environ.get(
                "LLM_TRAIT_SYNTH_MODEL", small_model
            ),
            llm_trait_synth_timeout_seconds=float(
                os.environ.get("LLM_TRAIT_SYNTH_TIMEOUT_SECONDS", "15")
            ),
            llm_trait_synth_max_tokens=int(
                os.environ.get("LLM_TRAIT_SYNTH_MAX_TOKENS", "1500")
            ),
            sqs_wait_seconds=int(os.environ.get("SQS_WAIT_SECONDS", "20")),
            db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "1")),
            db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "4")),
        )


@dataclass(frozen=True)
class ProfileSummaryConfig:
    """
    Configuration for the Profile Summary Generator worker (step 14).

    Sibling to :class:`TraitSynthesizerConfig`. Drains the
    ``profile_summary`` SQS queue (one message per person; producer is
    Session Wrap, step 16) and runs a single big-LLM (Sonnet) call per
    person to generate the prose ``persons.profile_summary`` text.

    A separate ``run-once --person-id <uuid>`` CLI path uses the same
    generation logic synchronously without the queue. For that path
    ``profile_summary_queue_url`` may be empty.

    Notes:

    * Profile summary is user-facing prose shown at the top of the
      legacy view, so it defaults to the ``LLM_BIG_*`` family (Sonnet).
    * Token budget is 600 (summaries are ~150–300 words; 600 leaves
      headroom). Hard timeout is 30s — generous because this is a
      background job, not a user-facing call.
    * Profile summaries themselves are display only (no embedding), but
      each profile-fact written by the extraction step pushes an
      ``embedding`` queue job — so this worker DOES need
      ``EMBEDDING_QUEUE_URL`` plus the embedding model identity.
    """

    database_url: str
    aws_region: str

    profile_summary_queue_url: str
    embedding_queue_url: str

    openai_api_key: str
    anthropic_api_key: str

    llm_profile_summary_provider: str
    llm_profile_summary_model: str
    llm_profile_summary_timeout_seconds: float
    llm_profile_summary_max_tokens: int

    # Profile-fact extraction is a small structured tool call; uses the
    # small-LLM family. Independent timeouts so profile-fact failures
    # don't drag the prose summary's budget.
    llm_profile_facts_provider: str
    llm_profile_facts_model: str
    llm_profile_facts_timeout_seconds: float
    llm_profile_facts_max_tokens: int

    embedding_model: str
    embedding_model_version: str

    profile_summary_top_traits_max: int
    profile_summary_top_threads_max: int
    profile_summary_top_entities_max: int

    sqs_wait_seconds: int
    db_pool_min_size: int
    db_pool_max_size: int

    @classmethod
    def from_env(cls, *, queue_required: bool = True) -> "ProfileSummaryConfig":
        big_provider = os.environ.get("LLM_BIG_PROVIDER", "anthropic")
        big_model = os.environ.get("LLM_BIG_MODEL", "claude-sonnet-4-6")
        small_provider = os.environ.get("LLM_SMALL_PROVIDER", "openai")
        small_model = os.environ.get("LLM_SMALL_MODEL", "gpt-5.1")
        queue_url = (
            _required("PROFILE_SUMMARY_QUEUE_URL")
            if queue_required
            else os.environ.get("PROFILE_SUMMARY_QUEUE_URL", "")
        )
        embedding_queue_url = (
            _required("EMBEDDING_QUEUE_URL")
            if queue_required
            else os.environ.get("EMBEDDING_QUEUE_URL", "")
        )
        return cls(
            database_url=_required("DATABASE_URL"),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            profile_summary_queue_url=queue_url,
            embedding_queue_url=embedding_queue_url,
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            anthropic_api_key=_required("ANTHROPIC_API_KEY"),
            llm_profile_summary_provider=os.environ.get(
                "LLM_PROFILE_SUMMARY_PROVIDER", big_provider
            ),
            llm_profile_summary_model=os.environ.get(
                "LLM_PROFILE_SUMMARY_MODEL", big_model
            ),
            llm_profile_summary_timeout_seconds=float(
                os.environ.get("LLM_PROFILE_SUMMARY_TIMEOUT_SECONDS", "30")
            ),
            llm_profile_summary_max_tokens=int(
                os.environ.get("LLM_PROFILE_SUMMARY_MAX_TOKENS", "600")
            ),
            llm_profile_facts_provider=os.environ.get(
                "LLM_PROFILE_FACTS_PROVIDER", small_provider
            ),
            llm_profile_facts_model=os.environ.get(
                "LLM_PROFILE_FACTS_MODEL", small_model
            ),
            llm_profile_facts_timeout_seconds=float(
                os.environ.get("LLM_PROFILE_FACTS_TIMEOUT_SECONDS", "15")
            ),
            llm_profile_facts_max_tokens=int(
                os.environ.get("LLM_PROFILE_FACTS_MAX_TOKENS", "800")
            ),
            embedding_model=os.environ.get("EMBEDDING_MODEL", "voyage-3-large"),
            embedding_model_version=os.environ.get(
                "EMBEDDING_MODEL_VERSION", "2025-01-07"
            ),
            profile_summary_top_traits_max=int(
                os.environ.get("PROFILE_SUMMARY_TOP_TRAITS_MAX", "7")
            ),
            profile_summary_top_threads_max=int(
                os.environ.get("PROFILE_SUMMARY_TOP_THREADS_MAX", "5")
            ),
            profile_summary_top_entities_max=int(
                os.environ.get("PROFILE_SUMMARY_TOP_ENTITIES_MAX", "8")
            ),
            sqs_wait_seconds=int(os.environ.get("SQS_WAIT_SECONDS", "20")),
            db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "1")),
            db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "4")),
        )


@dataclass(frozen=True)
class ProducerConfig:
    """
    Configuration for Question Producers P2/P3/P5 (step 15).

    ``run-per-session`` requires ``PRODUCERS_PER_SESSION_QUEUE_URL`` and
    only accepts P2 messages. ``run-weekly`` requires
    ``PRODUCERS_WEEKLY_QUEUE_URL`` and accepts P3/P5 messages. The CLI
    ``run-once`` path does not require either producer queue URL, but it
    still needs the embedding queue because new questions are embedded
    after commit.
    """

    database_url: str
    aws_region: str

    producers_per_session_queue_url: str
    producers_weekly_queue_url: str
    embedding_queue_url: str

    embedding_model: str
    embedding_model_version: str

    openai_api_key: str
    anthropic_api_key: str

    llm_producer_provider: str
    llm_producer_model: str
    llm_producer_timeout_seconds: float
    llm_producer_max_tokens: int

    p2_max_entities_per_run: int
    p2_questions_per_entity: int
    p3_max_gaps_per_run: int
    p3_questions_per_gap: int
    p5_max_dimensions_per_run: int
    p5_questions_per_dimension: int
    p5_dimension_coverage_threshold: int

    sqs_wait_seconds: int
    db_pool_min_size: int
    db_pool_max_size: int

    @classmethod
    def from_env(
        cls, *, queue_required: str | None = None
    ) -> "ProducerConfig":
        small_provider = os.environ.get("LLM_SMALL_PROVIDER", "openai")
        small_model = os.environ.get("LLM_SMALL_MODEL", "gpt-5.1")
        per_session_queue_url = (
            _required("PRODUCERS_PER_SESSION_QUEUE_URL")
            if queue_required == "per-session"
            else os.environ.get("PRODUCERS_PER_SESSION_QUEUE_URL", "")
        )
        weekly_queue_url = (
            _required("PRODUCERS_WEEKLY_QUEUE_URL")
            if queue_required == "weekly"
            else os.environ.get("PRODUCERS_WEEKLY_QUEUE_URL", "")
        )
        return cls(
            database_url=_required("DATABASE_URL"),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            producers_per_session_queue_url=per_session_queue_url,
            producers_weekly_queue_url=weekly_queue_url,
            embedding_queue_url=_required("EMBEDDING_QUEUE_URL"),
            embedding_model=os.environ.get("EMBEDDING_MODEL", "voyage-3-large"),
            embedding_model_version=os.environ.get(
                "EMBEDDING_MODEL_VERSION", "2025-01-07"
            ),
            openai_api_key=_required("OPENAI_API_KEY"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            llm_producer_provider=os.environ.get(
                "LLM_PRODUCER_PROVIDER", small_provider
            ),
            llm_producer_model=os.environ.get("LLM_PRODUCER_MODEL", small_model),
            llm_producer_timeout_seconds=float(
                os.environ.get("LLM_PRODUCER_TIMEOUT_SECONDS", "30")
            ),
            llm_producer_max_tokens=int(
                os.environ.get("LLM_PRODUCER_MAX_TOKENS", "3000")
            ),
            p2_max_entities_per_run=int(
                os.environ.get("P2_MAX_ENTITIES_PER_RUN", "3")
            ),
            p2_questions_per_entity=int(
                os.environ.get("P2_QUESTIONS_PER_ENTITY", "2")
            ),
            p3_max_gaps_per_run=int(os.environ.get("P3_MAX_GAPS_PER_RUN", "3")),
            p3_questions_per_gap=int(os.environ.get("P3_QUESTIONS_PER_GAP", "4")),
            p5_max_dimensions_per_run=int(
                os.environ.get("P5_MAX_DIMENSIONS_PER_RUN", "5")
            ),
            p5_questions_per_dimension=int(
                os.environ.get("P5_QUESTIONS_PER_DIMENSION", "2")
            ),
            p5_dimension_coverage_threshold=int(
                os.environ.get("P5_DIMENSION_COVERAGE_THRESHOLD", "3")
            ),
            sqs_wait_seconds=int(os.environ.get("SQS_WAIT_SECONDS", "20")),
            db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "1")),
            db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "4")),
        )


@dataclass(frozen=True)
class ThreadDetectorConfig:
    """
    Configuration for the Thread Detector worker (step 12).

    Sibling to :class:`ExtractionConfig`. Drains the ``thread_detector``
    SQS queue (pushed by the Extraction Worker post-commit), clusters
    moments via HDBSCAN, and writes new threads + P4 questions back.

    Two LLM calls per cluster (naming + P4) — both Sonnet by default.
    """

    database_url: str
    aws_region: str

    thread_detector_queue_url: str
    embedding_queue_url: str
    artifact_queue_url: str

    embedding_model: str
    embedding_model_version: str

    anthropic_api_key: str
    openai_api_key: str

    llm_thread_naming_provider: str
    llm_thread_naming_model: str
    llm_thread_naming_timeout_seconds: float
    llm_thread_naming_max_tokens: int

    llm_p4_provider: str
    llm_p4_model: str
    llm_p4_timeout_seconds: float
    llm_p4_max_tokens: int

    thread_detector_min_cluster_size: int
    thread_detector_existing_match_distance: float

    sqs_wait_seconds: int
    db_pool_min_size: int
    db_pool_max_size: int

    @classmethod
    def from_env(cls) -> "ThreadDetectorConfig":
        big_provider = os.environ.get("LLM_BIG_PROVIDER", "anthropic")
        big_model = os.environ.get("LLM_BIG_MODEL", "claude-sonnet-4-6")
        return cls(
            database_url=_required("DATABASE_URL"),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            thread_detector_queue_url=_required("THREAD_DETECTOR_QUEUE_URL"),
            embedding_queue_url=_required("EMBEDDING_QUEUE_URL"),
            artifact_queue_url=_required("ARTIFACT_QUEUE_URL"),
            embedding_model=os.environ.get("EMBEDDING_MODEL", "voyage-3-large"),
            embedding_model_version=os.environ.get(
                "EMBEDDING_MODEL_VERSION", "2025-01-07"
            ),
            anthropic_api_key=_required("ANTHROPIC_API_KEY"),
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            llm_thread_naming_provider=os.environ.get(
                "LLM_THREAD_NAMING_PROVIDER", big_provider
            ),
            llm_thread_naming_model=os.environ.get(
                "LLM_THREAD_NAMING_MODEL", big_model
            ),
            llm_thread_naming_timeout_seconds=float(
                os.environ.get("LLM_THREAD_NAMING_TIMEOUT_SECONDS", "30")
            ),
            llm_thread_naming_max_tokens=int(
                os.environ.get("LLM_THREAD_NAMING_MAX_TOKENS", "800")
            ),
            llm_p4_provider=os.environ.get("LLM_P4_PROVIDER", big_provider),
            llm_p4_model=os.environ.get("LLM_P4_MODEL", big_model),
            llm_p4_timeout_seconds=float(
                os.environ.get("LLM_P4_TIMEOUT_SECONDS", "30")
            ),
            llm_p4_max_tokens=int(os.environ.get("LLM_P4_MAX_TOKENS", "800")),
            thread_detector_min_cluster_size=int(
                os.environ.get("THREAD_DETECTOR_MIN_CLUSTER_SIZE", "3")
            ),
            thread_detector_existing_match_distance=float(
                os.environ.get("THREAD_DETECTOR_EXISTING_MATCH_DISTANCE", "0.4")
            ),
            sqs_wait_seconds=int(os.environ.get("SQS_WAIT_SECONDS", "20")),
            db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "1")),
            db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "4")),
        )
