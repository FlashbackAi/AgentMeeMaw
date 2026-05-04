"""Runner tests for P2/P3/P5 dispatch and idempotency."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from flashback.llm.errors import LLMError
from flashback.workers.producers import runner as runner_mod
from flashback.workers.producers.runner import run_once
from flashback.workers.producers.schema import GeneratedQuestion, ProducerResult

from tests.workers.producers.conftest import StubEmbeddingSender


class FakeProducer:
    name = "P2"
    source_tag = "life_period_gap"
    calls = 0

    async def produce(self, db_pool, person_id, settings):
        self.__class__.calls += 1
        return ProducerResult(
            person_id=person_id,
            source_tag="life_period_gap",
            overall_reasoning="ok",
            questions=[
                GeneratedQuestion(
                    text="What happened then?",
                    themes=["era"],
                    attributes={"life_period": "1960s"},
                )
            ],
        )


class FailingProducer:
    name = "P2"
    source_tag = "life_period_gap"

    async def produce(self, db_pool, person_id, settings):
        raise LLMError("bad tool")


async def test_dispatch_runs_selected_producer(
    db_pool, make_person, stub_settings, monkeypatch
) -> None:
    person_id = make_person("Dispatch")
    FakeProducer.calls = 0
    monkeypatch.setattr(runner_mod, "PRODUCERS_BY_TAG", {"P2": FakeProducer})
    sender = StubEmbeddingSender()

    result = await run_once(
        db_pool=db_pool,
        embedding_sender=sender,
        settings=stub_settings,
        producer_tag="P2",
        person_id=UUID(person_id),
        idempotency_key=f"k-{uuid4()}",
        embedding_model="voyage-3-large",
        embedding_model_version="2025-01-07",
    )

    assert FakeProducer.calls == 1
    assert result.persist is not None
    assert len(sender.sent) == 1


async def test_unknown_producer_raises(
    db_pool, make_person, stub_settings
) -> None:
    with pytest.raises(ValueError):
        await run_once(
            db_pool=db_pool,
            embedding_sender=StubEmbeddingSender(),
            settings=stub_settings,
            producer_tag="PX",
            person_id=UUID(make_person("Unknown")),
            idempotency_key=f"k-{uuid4()}",
            embedding_model="voyage-3-large",
            embedding_model_version="2025-01-07",
        )


async def test_idempotency_same_key_skips(
    db_pool, make_person, stub_settings, monkeypatch
) -> None:
    person_id = make_person("Idem")
    FakeProducer.calls = 0
    monkeypatch.setattr(runner_mod, "PRODUCERS_BY_TAG", {"P2": FakeProducer})
    sender = StubEmbeddingSender()
    key = f"k-{uuid4()}"

    first = await run_once(
        db_pool=db_pool,
        embedding_sender=sender,
        settings=stub_settings,
        producer_tag="P2",
        person_id=UUID(person_id),
        idempotency_key=key,
        embedding_model="voyage-3-large",
        embedding_model_version="2025-01-07",
    )
    second = await run_once(
        db_pool=db_pool,
        embedding_sender=sender,
        settings=stub_settings,
        producer_tag="P2",
        person_id=UUID(person_id),
        idempotency_key=key,
        embedding_model="voyage-3-large",
        embedding_model_version="2025-01-07",
    )

    assert first.skipped is False
    assert second.skipped is True
    assert FakeProducer.calls == 1


async def test_llm_error_marks_processed_with_zero_questions(
    db_pool, make_person, stub_settings, monkeypatch
) -> None:
    person_id = make_person("LLMError")
    key = f"k-{uuid4()}"
    monkeypatch.setattr(runner_mod, "PRODUCERS_BY_TAG", {"P2": FailingProducer})

    result = await run_once(
        db_pool=db_pool,
        embedding_sender=StubEmbeddingSender(),
        settings=stub_settings,
        producer_tag="P2",
        person_id=UUID(person_id),
        idempotency_key=key,
        embedding_model="voyage-3-large",
        embedding_model_version="2025-01-07",
    )

    assert result.error == "bad tool"
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT questions_written
                  FROM processed_producer_runs
                 WHERE idempotency_key=%s
                """,
                (key,),
            )
            assert cur.fetchone()[0] == 0

