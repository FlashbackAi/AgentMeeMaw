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
      (gpt-5-mini).
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
        small_model = os.environ.get("LLM_SMALL_MODEL", "gpt-5-mini")
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
                os.environ.get("LLM_COMPATIBILITY_MAX_TOKENS", "400")
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
    each process loads only what it needs. The HTTP service does not
    require ``EMBEDDING_QUEUE_URL`` or ``VOYAGE_API_KEY`` — those are
    embedding-worker concerns.
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
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    llm_small_provider: str = "openai"
    llm_small_model: str = "gpt-5-mini"
    llm_big_provider: str = "anthropic"
    llm_big_model: str = "claude-sonnet-4-6"
    llm_intent_model: str = "gpt-5-mini"
    llm_intent_timeout_seconds: float = 8.0
    llm_intent_max_tokens: int = 300
    llm_segment_detector_provider: str = "openai"
    llm_segment_detector_model: str = "gpt-5-mini"
    llm_segment_detector_timeout_seconds: float = 10.0
    llm_segment_detector_max_tokens: int = 600
    segment_detector_min_turns: int = 4
    llm_response_provider: str = "anthropic"
    llm_response_model: str = "claude-sonnet-4-6"
    llm_response_timeout_seconds: float = 12.0
    llm_response_max_tokens: int = 400
    extraction_queue_url: str = ""
    aws_region: str = "us-east-1"
    voyage_api_key: str = ""
    embedding_model: str = "voyage-3-large"
    embedding_model_version: str = "2025-01-07"
    retrieval_query_embed_timeout_seconds: float = 2.0
    retrieval_default_limit: int = 10
    retrieval_max_limit: int = 50

    @classmethod
    def from_env(cls) -> "HttpConfig":
        llm_small_model = os.environ.get("LLM_SMALL_MODEL", "gpt-5-mini")
        llm_big_model = os.environ.get("LLM_BIG_MODEL", "claude-sonnet-4-6")
        return cls(
            database_url=_required("DATABASE_URL"),
            valkey_url=_required("VALKEY_URL"),
            service_token=_required("SERVICE_TOKEN"),
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
            llm_intent_max_tokens=int(os.environ.get("LLM_INTENT_MAX_TOKENS", "300")),
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
                os.environ.get("LLM_SEGMENT_DETECTOR_MAX_TOKENS", "600")
            ),
            segment_detector_min_turns=int(
                os.environ.get("SEGMENT_DETECTOR_MIN_TURNS", "4")
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
        small_model = os.environ.get("LLM_SMALL_MODEL", "gpt-5-mini")
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
