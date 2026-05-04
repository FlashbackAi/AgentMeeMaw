"""Shared fixtures for the Trait Synthesizer tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from flashback.workers.extraction.sqs_client import EmbeddingJobSender
from flashback.workers.trait_synthesizer.schema import TraitSynthMessage
from flashback.workers.trait_synthesizer.sqs_client import (
    ReceivedTraitSynthMessage,
    TraitSynthesizerSQSClient,
)
from flashback.workers.trait_synthesizer.synth_llm import SynthLLMConfig


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
class StubTraitSynthSQSClient(TraitSynthesizerSQSClient):
    queue_url: str = "stub://trait_synthesizer"
    region_name: str = "us-east-1"
    pending: list[ReceivedTraitSynthMessage] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def receive(self, *, wait_seconds: int = 20):  # type: ignore[override]
        out = list(self.pending)
        self.pending.clear()
        return out

    def delete(self, receipt_handle: str) -> None:  # type: ignore[override]
        self.deleted.append(receipt_handle)


def make_trait_synth_message(
    *,
    person_id: str,
    receipt_handle: str | None = None,
    message_id: str | None = None,
) -> ReceivedTraitSynthMessage:
    payload = TraitSynthMessage.model_validate({"person_id": person_id})
    return ReceivedTraitSynthMessage(
        message_id=message_id or f"ts-{uuid4()}",
        receipt_handle=receipt_handle or f"rh-{uuid4()}",
        payload=payload,
        raw_body=json.dumps(payload.model_dump(mode="json")),
    )


# ---------------------------------------------------------------------------
# LLM config + settings stubs
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_synth_cfg() -> SynthLLMConfig:
    return SynthLLMConfig(
        provider="openai",
        model="gpt-5-mini",
        timeout=10.0,
        max_tokens=1000,
    )


@pytest.fixture
def stub_settings():
    class S:
        anthropic_api_key = "stub"
        openai_api_key = "stub"

    return S()


def queued_call_with_tool(items: list[dict]):
    """Async stub for ``call_with_tool`` that pops dicts off a queue."""
    seq = list(items)

    async def _impl(**kwargs):
        if not seq:
            raise AssertionError("queued_call_with_tool ran out of responses")
        return seq.pop(0)

    return _impl


def failing_call_with_tool(exc: Exception):
    """Async stub that always raises the supplied exception."""

    async def _impl(**kwargs):
        raise exc

    return _impl
