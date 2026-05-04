"""Worker tests for producer queue ack/no-ack behavior."""

from __future__ import annotations

from uuid import uuid4

from flashback.llm.errors import LLMTimeout
from flashback.workers.producers import life_period as p3_mod
from flashback.workers.producers import underdeveloped as p2_mod
from flashback.workers.producers import universal as p5_mod
from flashback.workers.producers import worker as worker_mod
from flashback.workers.producers.runner import RunResult
from flashback.workers.producers.worker import ProducerWorker

from tests.workers.producers.conftest import (
    StubEmbeddingSender,
    StubProducerSQSClient,
    make_producer_message,
    queued_call_with_tool,
    seed_entity,
    seed_moment,
)
from tests.workers.producers.fixtures.sample_states import p2_result, p3_result, p5_result


def _worker(db_pool, stub_settings, *, allowed):
    return ProducerWorker(
        db_pool=db_pool,
        sqs=StubProducerSQSClient(),
        embedding_sender=StubEmbeddingSender(),
        settings=stub_settings,
        allowed_producers=frozenset(allowed),
        embedding_model="voyage-3-large",
        embedding_model_version="2025-01-07",
    )


def test_per_session_worker_runs_p2_and_acks(
    db_pool, make_person, stub_settings, monkeypatch
):
    person_id = make_person("P2 worker")
    entity_id = seed_entity(db_pool, person_id=person_id, name="Uncle Raj")
    monkeypatch.setattr(p2_mod, "call_with_tool", queued_call_with_tool([p2_result(entity_id)]))
    worker = _worker(db_pool, stub_settings, allowed={"P2"})
    msg = make_producer_message(person_id=person_id, producer="P2")

    result = worker.process_message(msg)

    assert result is not None and result.persist is not None
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]
    assert len(worker.embedding_sender.sent) == 1  # type: ignore[attr-defined]


def test_weekly_worker_runs_p3_and_p5(
    db_pool, make_person, stub_settings, monkeypatch
):
    p3_person = make_person("P3 worker")
    seed_moment(db_pool, person_id=p3_person, year=1950)
    seed_moment(db_pool, person_id=p3_person, year=1970)
    p5_person = make_person("P5 worker")
    monkeypatch.setattr(p3_mod, "call_with_tool", queued_call_with_tool([p3_result("1960s")]))
    monkeypatch.setattr(p5_mod, "call_with_tool", queued_call_with_tool([p5_result("childhood")]))
    worker = _worker(db_pool, stub_settings, allowed={"P3", "P5"})

    r3 = worker.process_message(make_producer_message(person_id=p3_person, producer="P3"))
    r5 = worker.process_message(make_producer_message(person_id=p5_person, producer="P5"))

    assert r3 is not None and r3.persist is not None
    assert r5 is not None and r5.persist is not None
    assert len(worker.sqs.deleted) == 2  # type: ignore[attr-defined]


def test_llm_timeout_does_not_ack(db_pool, make_person, stub_settings, monkeypatch):
    async def _timeout(**kwargs):
        raise LLMTimeout("slow")

    monkeypatch.setattr(worker_mod, "run_once", _timeout)
    worker = _worker(db_pool, stub_settings, allowed={"P2"})
    msg = make_producer_message(person_id=make_person("Slow"), producer="P2")

    result = worker.process_message(msg)

    assert result is None
    assert worker.sqs.deleted == []  # type: ignore[attr-defined]


def test_generic_exception_does_not_ack(db_pool, make_person, stub_settings, monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_mod, "run_once", _boom)
    worker = _worker(db_pool, stub_settings, allowed={"P2"})
    msg = make_producer_message(person_id=make_person("Boom"), producer="P2")

    result = worker.process_message(msg)

    assert result is None
    assert worker.sqs.deleted == []  # type: ignore[attr-defined]


def test_worker_acks_successful_empty_result(db_pool, make_person, stub_settings, monkeypatch):
    async def _empty(**kwargs):
        return RunResult.empty_result()

    monkeypatch.setattr(worker_mod, "run_once", _empty)
    worker = _worker(db_pool, stub_settings, allowed={"P2"})
    msg = make_producer_message(person_id=make_person("Empty"), producer="P2")

    result = worker.process_message(msg)

    assert result is not None and result.empty is True
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]

