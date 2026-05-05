"""Shared fixtures for P2/P3/P5 producer tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from psycopg.types.json import Json

from flashback.workers.extraction.sqs_client import EmbeddingJobSender
from flashback.workers.producers.schema import ProducerMessage
from flashback.workers.producers.sqs_client import (
    ProducerSQSClient,
    ReceivedProducerMessage,
)


@dataclass
class StubEmbeddingSender(EmbeddingJobSender):
    queue_url: str = "stub://embedding"
    region_name: str = "us-east-1"
    sent: list[dict] = field(default_factory=list)

    def send(self, **kwargs) -> str:  # type: ignore[override]
        self.sent.append(dict(kwargs))
        return f"emb-{len(self.sent)}"


@dataclass
class StubProducerSQSClient(ProducerSQSClient):
    queue_url: str = "stub://producer"
    region_name: str = "us-east-1"
    pending: list[ReceivedProducerMessage] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def receive(self, *, wait_seconds: int = 20):  # type: ignore[override]
        out = list(self.pending)
        self.pending.clear()
        return out

    def delete(self, receipt_handle: str) -> None:  # type: ignore[override]
        self.deleted.append(receipt_handle)


def make_producer_message(
    *,
    person_id: str,
    producer: str = "P2",
    receipt_handle: str | None = None,
    message_id: str | None = None,
) -> ReceivedProducerMessage:
    payload = ProducerMessage.model_validate(
        {"person_id": person_id, "producer": producer}
    )
    return ReceivedProducerMessage(
        message_id=message_id or f"prod-{uuid4()}",
        receipt_handle=receipt_handle or f"rh-{uuid4()}",
        payload=payload,
        raw_body=json.dumps(payload.model_dump(mode="json")),
    )


@pytest.fixture
def stub_settings():
    class S:
        openai_api_key = "stub"
        anthropic_api_key = "stub"
        llm_producer_provider = "openai"
        llm_producer_model = "gpt-5.1"
        llm_producer_timeout_seconds = 15.0
        llm_producer_max_tokens = 1500
        p2_max_entities_per_run = 3
        p2_questions_per_entity = 2
        p3_max_gaps_per_run = 3
        p3_questions_per_gap = 4
        p5_max_dimensions_per_run = 5
        p5_questions_per_dimension = 2
        p5_dimension_coverage_threshold = 3

    return S()


def queued_call_with_tool(items: list[dict]):
    seq = list(items)

    async def _impl(**kwargs):
        if not seq:
            raise AssertionError("queued_call_with_tool ran out of responses")
        return seq.pop(0)

    return _impl


def failing_call_with_tool(exc: Exception):
    async def _impl(**kwargs):
        raise exc

    return _impl


def seed_entity(db_pool, *, person_id: str, name: str, kind: str = "person", description: str | None = None) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entities (person_id, kind, name, description)
                VALUES (%s, %s, %s, %s)
                RETURNING id::text
                """,
                (person_id, kind, name, description),
            )
            eid = cur.fetchone()[0]
            conn.commit()
    return eid


def seed_moment(
    db_pool,
    *,
    person_id: str,
    title: str = "Moment",
    narrative: str = "Narrative",
    year: int | None = None,
    life_period_estimate: str | None = None,
) -> str:
    time_anchor = Json({"year": year}) if year is not None else None
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments
                      (person_id, title, narrative, time_anchor, life_period_estimate)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (person_id, title, narrative, time_anchor, life_period_estimate),
            )
            mid = cur.fetchone()[0]
            conn.commit()
    return mid


def seed_thread(
    db_pool, *, person_id: str, name: str = "Thread", description: str = "Description"
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO threads (person_id, name, description)
                VALUES (%s, %s, %s)
                RETURNING id::text
                """,
                (person_id, name, description),
            )
            tid = cur.fetchone()[0]
            conn.commit()
    return tid


def seed_edge(
    db_pool,
    *,
    from_kind: str,
    from_id: str,
    to_kind: str,
    to_id: str,
    edge_type: str,
) -> None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO edges (from_kind, from_id, to_kind, to_id, edge_type)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (from_kind, from_id, to_kind, to_id, edge_type),
            )
            conn.commit()

