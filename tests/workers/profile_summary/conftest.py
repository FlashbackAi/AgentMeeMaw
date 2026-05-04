"""Shared fixtures for the Profile Summary tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from flashback.workers.profile_summary.schema import ProfileSummaryMessage
from flashback.workers.profile_summary.sqs_client import (
    ProfileSummarySQSClient,
    ReceivedProfileSummaryMessage,
)
from flashback.workers.profile_summary.summary_llm import SummaryLLMConfig


# ---------------------------------------------------------------------------
# SQS stub
# ---------------------------------------------------------------------------


@dataclass
class StubProfileSummarySQSClient(ProfileSummarySQSClient):
    queue_url: str = "stub://profile_summary"
    region_name: str = "us-east-1"
    pending: list[ReceivedProfileSummaryMessage] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def receive(self, *, wait_seconds: int = 20):  # type: ignore[override]
        out = list(self.pending)
        self.pending.clear()
        return out

    def delete(self, receipt_handle: str) -> None:  # type: ignore[override]
        self.deleted.append(receipt_handle)


def make_profile_summary_message(
    *,
    person_id: str,
    receipt_handle: str | None = None,
    message_id: str | None = None,
) -> ReceivedProfileSummaryMessage:
    payload = ProfileSummaryMessage.model_validate({"person_id": person_id})
    return ReceivedProfileSummaryMessage(
        message_id=message_id or f"ps-{uuid4()}",
        receipt_handle=receipt_handle or f"rh-{uuid4()}",
        payload=payload,
        raw_body=json.dumps(payload.model_dump(mode="json")),
    )


# ---------------------------------------------------------------------------
# LLM config + settings stubs
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_summary_cfg() -> SummaryLLMConfig:
    return SummaryLLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        timeout=30.0,
        max_tokens=600,
    )


@pytest.fixture
def stub_settings():
    class S:
        anthropic_api_key = "stub"
        openai_api_key = "stub"

    return S()


# ---------------------------------------------------------------------------
# call_text stubs
# ---------------------------------------------------------------------------


def queued_call_text(items: list[str]):
    """Async stub for ``call_text`` that pops strings off a queue."""
    seq = list(items)

    async def _impl(**kwargs):
        if not seq:
            raise AssertionError("queued_call_text ran out of responses")
        return seq.pop(0)

    return _impl


def failing_call_text(exc: Exception):
    """Async stub that always raises the supplied exception."""

    async def _impl(**kwargs):
        raise exc

    return _impl


# ---------------------------------------------------------------------------
# Tuning fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def top_caps():
    """Default top-N caps used in tests. Match the prompt's defaults."""
    return {
        "top_traits_max": 7,
        "top_threads_max": 5,
        "top_entities_max": 8,
    }
