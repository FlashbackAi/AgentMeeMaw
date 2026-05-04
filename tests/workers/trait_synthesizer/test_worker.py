"""End-to-end Trait Synthesizer worker tests (DB-touching)."""

from __future__ import annotations

from uuid import uuid4

from flashback.llm.errors import LLMError, LLMMalformedResponse, LLMTimeout
from flashback.workers.trait_synthesizer import synth_llm as synth_mod
from flashback.workers.trait_synthesizer.worker import TraitSynthesizerWorker

from tests.workers.trait_synthesizer.conftest import (
    StubEmbeddingSender,
    StubTraitSynthSQSClient,
    failing_call_with_tool,
    make_trait_synth_message,
    queued_call_with_tool,
)
from tests.workers.trait_synthesizer.fixtures.sample_states import (
    new_trait_proposal,
    synthesis_result,
)


MODEL = "voyage-3-large"
VERSION = "2025-01-07"


def _seed_thread(db_pool, *, person_id, name="thread"):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO threads (person_id, name, description)
                VALUES (%s, %s, %s) RETURNING id::text
                """,
                (person_id, name, "d"),
            )
            tid = cur.fetchone()[0]
            conn.commit()
    return tid


def _build_worker(db_pool, stub_synth_cfg, stub_settings) -> TraitSynthesizerWorker:
    return TraitSynthesizerWorker(
        db_pool=db_pool,
        sqs=StubTraitSynthSQSClient(),
        embedding_sender=StubEmbeddingSender(),
        synth_cfg=stub_synth_cfg,
        settings=stub_settings,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )


def test_happy_path_writes_and_acks(
    db_pool, make_person, monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    person_id = make_person("Happy worker")
    thread_id = _seed_thread(db_pool, person_id=person_id)

    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                synthesis_result(
                    new_proposals=[
                        new_trait_proposal(
                            name="Generous worker trait",
                            description="x",
                            thread_ids=[thread_id],
                        )
                    ]
                )
            ]
        ),
    )

    worker = _build_worker(db_pool, stub_synth_cfg, stub_settings)
    msg = make_trait_synth_message(person_id=person_id)

    result = worker.process_message(msg)
    assert result is not None
    assert result.skipped is False
    assert result.persist.created_count == 1  # type: ignore[union-attr]
    # Acked.
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]
    # Embedding push happened.
    assert len(worker.embedding_sender.sent) == 1  # type: ignore[attr-defined]
    # DB has the trait.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM traits
                    WHERE person_id=%s AND name='Generous worker trait'""",
                (person_id,),
            )
            assert cur.fetchone()[0] == 1


def test_llm_timeout_does_not_ack(
    db_pool, make_person, monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    person_id = make_person("Slow")
    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        failing_call_with_tool(LLMTimeout("slow")),
    )
    worker = _build_worker(db_pool, stub_synth_cfg, stub_settings)
    msg = make_trait_synth_message(person_id=person_id)

    result = worker.process_message(msg)
    assert result is None
    assert worker.sqs.deleted == []  # type: ignore[attr-defined]


def test_llm_malformed_response_acks(
    db_pool, make_person, monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    """Permanent LLM error → fail-soft, ack the message."""
    person_id = make_person("Malformed")
    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        failing_call_with_tool(LLMMalformedResponse("garbage")),
    )
    worker = _build_worker(db_pool, stub_synth_cfg, stub_settings)
    msg = make_trait_synth_message(person_id=person_id)

    result = worker.process_message(msg)
    assert result is None
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]


def test_base_llm_error_acks(
    db_pool, make_person, monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    """Generic LLMError (not Timeout) is also a permanent ack."""
    person_id = make_person("BaseLLM")
    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        failing_call_with_tool(LLMError("boom")),
    )
    worker = _build_worker(db_pool, stub_synth_cfg, stub_settings)
    msg = make_trait_synth_message(person_id=person_id)

    result = worker.process_message(msg)
    assert result is None
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]


def test_generic_exception_does_not_ack(
    db_pool, make_person, monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    """Unexpected exception → SQS redrives."""
    person_id = make_person("Boom")

    from flashback.workers.trait_synthesizer import runner as runner_mod

    def _boom(**kwargs):
        raise RuntimeError("DB blew up")

    monkeypatch.setattr(runner_mod, "build_context", _boom)

    worker = _build_worker(db_pool, stub_synth_cfg, stub_settings)
    msg = make_trait_synth_message(person_id=person_id)
    result = worker.process_message(msg)
    assert result is None
    assert worker.sqs.deleted == []  # type: ignore[attr-defined]


def test_idempotency_redelivery_skips_and_acks(
    db_pool, make_person, monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    """Same MessageId twice → second is a skip and still acked."""
    person_id = make_person("Redrive")
    thread_id = _seed_thread(db_pool, person_id=person_id)

    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                synthesis_result(
                    new_proposals=[
                        new_trait_proposal(
                            name="Idem worker trait",
                            description="x",
                            thread_ids=[thread_id],
                        )
                    ]
                )
            ]
        ),
    )

    worker = _build_worker(db_pool, stub_synth_cfg, stub_settings)
    shared_message_id = f"msg-{uuid4()}"
    first = make_trait_synth_message(
        person_id=person_id, message_id=shared_message_id, receipt_handle="rh-1"
    )
    second = make_trait_synth_message(
        person_id=person_id, message_id=shared_message_id, receipt_handle="rh-2"
    )

    r1 = worker.process_message(first)
    r2 = worker.process_message(second)

    assert r1 is not None and r1.skipped is False
    assert r2 is not None and r2.skipped is True
    # Both acked.
    assert worker.sqs.deleted == ["rh-1", "rh-2"]  # type: ignore[attr-defined]
    # Embedding push only happened once.
    assert len(worker.embedding_sender.sent) == 1  # type: ignore[attr-defined]
