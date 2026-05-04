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
