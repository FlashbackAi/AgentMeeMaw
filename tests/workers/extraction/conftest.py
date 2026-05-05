"""
Shared fixtures for the extraction-worker tests.

The DB-touching tests reuse ``db_pool`` from ``tests/conftest.py`` (which
applies all ``migrations/*.up.sql`` files in order, so 0003 is picked up
automatically).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from flashback.workers.extraction.compatibility_llm import (
    CompatibilityLLMConfig,
    CompatibilityResponse,
)
from flashback.workers.extraction.extraction_llm import ExtractionLLMConfig
from flashback.workers.extraction.refinement import RefinementCandidate
from flashback.workers.extraction.schema import ExtractionMessage
from flashback.workers.extraction.sqs_client import (
    ArtifactJobSender,
    EmbeddingJobSender,
    ExtractionSQSClient,
    ReceivedMessage,
)
from flashback.workers.thread_detector.sqs_client import ThreadDetectorJobSender


# ---------------------------------------------------------------------------
# LLM stubs (mock the async call_with_tool surface)
# ---------------------------------------------------------------------------


@dataclass
class StubLLM:
    """Captures invocations and returns a queued list of dicts."""

    queued: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if not self.queued:
            raise AssertionError("StubLLM ran out of queued responses")
        return self.queued.pop(0)


# ---------------------------------------------------------------------------
# SQS stubs
# ---------------------------------------------------------------------------


@dataclass
class StubSQSEmbeddingSender(EmbeddingJobSender):
    """Records sent embedding payloads. Inherits real signature."""

    queue_url: str = "stub://embedding"
    region_name: str = "us-east-1"
    sent: list[dict] = field(default_factory=list)

    def send(self, **kwargs) -> str:  # type: ignore[override]
        self.sent.append(dict(kwargs))
        return f"msg-emb-{len(self.sent)}"


@dataclass
class StubSQSArtifactSender(ArtifactJobSender):
    queue_url: str = "stub://artifact"
    region_name: str = "us-east-1"
    sent: list[dict] = field(default_factory=list)

    def send(self, **kwargs) -> str:  # type: ignore[override]
        self.sent.append(dict(kwargs))
        return f"msg-art-{len(self.sent)}"


@dataclass
class StubSQSThreadDetectorSender(ThreadDetectorJobSender):
    queue_url: str = "stub://thread_detector"
    region_name: str = "us-east-1"
    sent: list[dict] = field(default_factory=list)

    def send(self, **kwargs) -> str:  # type: ignore[override]
        self.sent.append(dict(kwargs))
        return f"msg-td-{len(self.sent)}"


@dataclass
class StubExtractionSQSClient(ExtractionSQSClient):
    queue_url: str = "stub://extraction"
    region_name: str = "us-east-1"
    pending: list[ReceivedMessage] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def receive(self, *, wait_seconds: int = 20):  # type: ignore[override]
        out = list(self.pending)
        self.pending.clear()
        return out

    def delete(self, receipt_handle: str) -> None:  # type: ignore[override]
        self.deleted.append(receipt_handle)


# ---------------------------------------------------------------------------
# Voyage stub
# ---------------------------------------------------------------------------


class StubVoyage:
    """Returns a deterministic vector or ``None`` to simulate failure."""

    def __init__(
        self,
        vector: list[float] | None = None,
        return_none: bool = False,
    ) -> None:
        self.vector = vector or ([0.5] * 1024)
        self.return_none = return_none
        self.calls: list[str] = []

    def embed(self, query: str) -> list[float] | None:
        self.calls.append(query)
        if self.return_none:
            return None
        return list(self.vector)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_received_message(
    *,
    person_id: str,
    session_id: str | None = None,
    seeded_question_id: str | None = None,
    segment_turns: list[dict] | None = None,
    rolling_summary: str = "",
    prior_rolling_summary: str = "",
    receipt_handle: str | None = None,
    message_id: str | None = None,
) -> ReceivedMessage:
    """Build a fully-typed :class:`ReceivedMessage` for tests."""
    payload = ExtractionMessage.model_validate(
        {
            "session_id": session_id or str(uuid4()),
            "person_id": person_id,
            "segment_turns": segment_turns
            or [
                {
                    "role": "assistant",
                    "content": "Tell me about him.",
                    "timestamp": "2026-05-04T12:00:00+00:00",
                },
                {
                    "role": "user",
                    "content": "He was warm. Always made pancakes Sundays.",
                    "timestamp": "2026-05-04T12:00:30+00:00",
                },
            ],
            "rolling_summary": rolling_summary,
            "prior_rolling_summary": prior_rolling_summary,
            "seeded_question_id": seeded_question_id,
        }
    )
    return ReceivedMessage(
        message_id=message_id or f"msg-{uuid4()}",
        receipt_handle=receipt_handle or f"rh-{uuid4()}",
        payload=payload,
        raw_body=json.dumps(payload.model_dump(mode="json")),
    )


@pytest.fixture
def stub_extraction_cfg() -> ExtractionLLMConfig:
    return ExtractionLLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        timeout=10.0,
        max_tokens=4000,
    )


@pytest.fixture
def stub_compat_cfg() -> CompatibilityLLMConfig:
    return CompatibilityLLMConfig(
        provider="openai",
        model="gpt-5.1",
        timeout=5.0,
        max_tokens=200,
    )


@pytest.fixture
def stub_settings():
    """Minimal settings shape that LLM clients want."""

    class S:
        openai_api_key = "stub"
        anthropic_api_key = "stub"

    return S()
