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
