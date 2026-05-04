"""Shared fixtures for the Thread Detector tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from flashback.workers.extraction.sqs_client import (
    ArtifactJobSender,
    EmbeddingJobSender,
)
from flashback.workers.thread_detector.naming_llm import NamingLLMConfig
from flashback.workers.thread_detector.p4_llm import P4LLMConfig
from flashback.workers.thread_detector.schema import ThreadDetectorMessage
from flashback.workers.thread_detector.sqs_client import (
    ReceivedThreadDetectorMessage,
    ThreadDetectorSQSClient,
)


# ---------------------------------------------------------------------------
# SQS stubs
# ---------------------------------------------------------------------------


@dataclass
class StubEmbeddingSender(EmbeddingJobSender):
    queue_url: str = "stub://embedding"
    region_name: str = "us-east-1"
    sent: list[dict] = field(default_factory=list)

    def send(self, **kwargs) -> str:  # type: ignore[override]
        self.sent.append(dict(kwargs))
        return f"emb-{len(self.sent)}"


@dataclass
class StubArtifactSender(ArtifactJobSender):
    queue_url: str = "stub://artifact"
    region_name: str = "us-east-1"
    sent: list[dict] = field(default_factory=list)

    def send(self, **kwargs) -> str:  # type: ignore[override]
        self.sent.append(dict(kwargs))
        return f"art-{len(self.sent)}"


@dataclass
class StubThreadDetectorSQSClient(ThreadDetectorSQSClient):
    queue_url: str = "stub://thread_detector"
    region_name: str = "us-east-1"
    pending: list[ReceivedThreadDetectorMessage] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def receive(self, *, wait_seconds: int = 20):  # type: ignore[override]
        out = list(self.pending)
        self.pending.clear()
        return out

    def delete(self, receipt_handle: str) -> None:  # type: ignore[override]
        self.deleted.append(receipt_handle)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_thread_detector_message(
    *,
    person_id: str,
    active_count: int = 15,
    last_count: int = 0,
    receipt_handle: str | None = None,
    message_id: str | None = None,
) -> ReceivedThreadDetectorMessage:
    payload = ThreadDetectorMessage.model_validate(
        {
            "person_id": person_id,
            "active_count_at_trigger": active_count,
            "last_count_at_trigger": last_count,
        }
    )
    return ReceivedThreadDetectorMessage(
        message_id=message_id or f"td-{uuid4()}",
        receipt_handle=receipt_handle or f"rh-{uuid4()}",
        payload=payload,
        raw_body=json.dumps(payload.model_dump(mode="json")),
    )


# ---------------------------------------------------------------------------
# LLM config + stubs
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_naming_cfg() -> NamingLLMConfig:
    return NamingLLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        timeout=10.0,
        max_tokens=600,
    )


@pytest.fixture
def stub_p4_cfg() -> P4LLMConfig:
    return P4LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        timeout=10.0,
        max_tokens=600,
    )


@pytest.fixture
def stub_settings():
    class S:
        anthropic_api_key = "stub"
        openai_api_key = "stub"

    return S()


def queued_call_with_tool(items: list[dict]):
    """Async stub for ``call_with_tool`` that pops dicts off a queue.

    A test can pre-load alternating naming + P4 responses, monkeypatch
    both modules' ``call_with_tool`` to this stub, and the worker will
    pull them in order.
    """
    seq = list(items)

    async def _impl(**kwargs):
        if not seq:
            raise AssertionError("queued_call_with_tool ran out of responses")
        return seq.pop(0)

    return _impl
