"""End-to-end Profile Summary worker tests (DB-touching)."""

from __future__ import annotations

from uuid import uuid4

from flashback.llm.errors import LLMError, LLMMalformedResponse, LLMTimeout
from flashback.workers.profile_summary import summary_llm as summary_mod
from flashback.workers.profile_summary.worker import ProfileSummaryWorker

from tests.workers.profile_summary.conftest import (
    StubProfileSummarySQSClient,
    failing_call_text,
    make_profile_summary_message,
    queued_call_text,
)
from tests.workers.profile_summary.fixtures.sample_profiles import (
    seed_rich_profile,
)


def _build_worker(db_pool, stub_summary_cfg, stub_settings) -> ProfileSummaryWorker:
    return ProfileSummaryWorker(
        db_pool=db_pool,
        sqs=StubProfileSummarySQSClient(),
        summary_cfg=stub_summary_cfg,
        settings=stub_settings,
        top_traits_max=7,
        top_threads_max=5,
        top_entities_max=8,
    )


def _profile_summary(db_pool, person_id: str) -> str | None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT profile_summary FROM persons WHERE id=%s", (person_id,)
            )
            row = cur.fetchone()
    return row[0] if row else None


def test_happy_path_writes_summary_and_acks(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings
):
    person_id = make_person("HappyWorker")
    seed_rich_profile(db_pool, person_id)

    monkeypatch.setattr(
        summary_mod,
        "call_text",
        queued_call_text(["A worker-written summary."]),
    )

    worker = _build_worker(db_pool, stub_summary_cfg, stub_settings)
    msg = make_profile_summary_message(person_id=person_id)

    result = worker.process_message(msg)
    assert result is not None
    assert result.skipped is False
    assert result.empty is False
    assert result.persist is not None
    # Acked.
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]
    # DB has the summary.
    assert _profile_summary(db_pool, person_id) == "A worker-written summary."


def test_llm_timeout_does_not_ack(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings
):
    person_id = make_person("Slow")
    seed_rich_profile(db_pool, person_id)

    monkeypatch.setattr(
        summary_mod, "call_text", failing_call_text(LLMTimeout("slow"))
    )

    worker = _build_worker(db_pool, stub_summary_cfg, stub_settings)
    msg = make_profile_summary_message(person_id=person_id)

    result = worker.process_message(msg)
    assert result is None
    assert worker.sqs.deleted == []  # type: ignore[attr-defined]
    # No summary written.
    assert _profile_summary(db_pool, person_id) is None


def test_llm_malformed_response_acks(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings
):
    """Permanent LLM error → fail-soft, ack the message."""
    person_id = make_person("Malformed")
    seed_rich_profile(db_pool, person_id)

    monkeypatch.setattr(
        summary_mod,
        "call_text",
        failing_call_text(LLMMalformedResponse("garbage")),
    )
    worker = _build_worker(db_pool, stub_summary_cfg, stub_settings)
    msg = make_profile_summary_message(person_id=person_id)

    result = worker.process_message(msg)
    assert result is None
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]


def test_base_llm_error_acks(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings
):
    """Generic LLMError (not Timeout) is also a permanent ack."""
    person_id = make_person("BaseLLM")
    seed_rich_profile(db_pool, person_id)

    monkeypatch.setattr(
        summary_mod, "call_text", failing_call_text(LLMError("boom"))
    )
    worker = _build_worker(db_pool, stub_summary_cfg, stub_settings)
    msg = make_profile_summary_message(person_id=person_id)

    result = worker.process_message(msg)
    assert result is None
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]


def test_generic_exception_does_not_ack(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings
):
    """Unexpected exception → SQS redrives."""
    person_id = make_person("Boom")

    from flashback.workers.profile_summary import runner as runner_mod

    def _boom(**kwargs):
        raise RuntimeError("DB blew up")

    monkeypatch.setattr(runner_mod, "build_context", _boom)

    worker = _build_worker(db_pool, stub_summary_cfg, stub_settings)
    msg = make_profile_summary_message(person_id=person_id)
    result = worker.process_message(msg)
    assert result is None
    assert worker.sqs.deleted == []  # type: ignore[attr-defined]


def test_idempotency_redelivery_skips_and_acks(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings
):
    """Same MessageId twice → second is a skip and still acked."""
    person_id = make_person("Redrive")
    seed_rich_profile(db_pool, person_id)

    monkeypatch.setattr(
        summary_mod,
        "call_text",
        queued_call_text(["Just one summary."]),
    )

    worker = _build_worker(db_pool, stub_summary_cfg, stub_settings)
    shared_message_id = f"msg-{uuid4()}"
    first = make_profile_summary_message(
        person_id=person_id, message_id=shared_message_id, receipt_handle="rh-1"
    )
    second = make_profile_summary_message(
        person_id=person_id, message_id=shared_message_id, receipt_handle="rh-2"
    )

    r1 = worker.process_message(first)
    r2 = worker.process_message(second)

    assert r1 is not None and r1.skipped is False
    assert r2 is not None and r2.skipped is True
    # Both acked.
    assert worker.sqs.deleted == ["rh-1", "rh-2"]  # type: ignore[attr-defined]
    # Summary written exactly once.
    assert _profile_summary(db_pool, person_id) == "Just one summary."


def test_fresh_summary_overwrites_stale_one(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings
):
    """A new MessageId for the same person produces a fresh summary
    that overwrites the prior one (consecutive session wraps)."""
    person_id = make_person("Fresh")
    seed_rich_profile(db_pool, person_id)

    monkeypatch.setattr(
        summary_mod,
        "call_text",
        queued_call_text(["older", "newer"]),
    )

    worker = _build_worker(db_pool, stub_summary_cfg, stub_settings)
    worker.process_message(make_profile_summary_message(person_id=person_id))
    worker.process_message(make_profile_summary_message(person_id=person_id))

    assert _profile_summary(db_pool, person_id) == "newer"


def test_empty_legacy_acks_without_llm_call(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings
):
    """No traits/threads/entities → empty short-circuit, no LLM, ack."""
    person_id = make_person("EmptyWorker")

    def _boom(**kwargs):
        raise AssertionError("LLM must not be called for an empty legacy")

    monkeypatch.setattr(summary_mod, "call_text", _boom)

    worker = _build_worker(db_pool, stub_summary_cfg, stub_settings)
    msg = make_profile_summary_message(person_id=person_id)
    result = worker.process_message(msg)

    assert result is not None
    assert result.empty is True
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]
    assert _profile_summary(db_pool, person_id) is None
