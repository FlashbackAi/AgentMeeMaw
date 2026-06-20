"""Unit tests for the tribute_render worker orchestration (deps mocked)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flashback.workers.tribute_render.sqs_client import (
    TributeRenderMessage,
    _parse_message,
)
from flashback.workers.tribute_render.worker import handle_failure, process_one


def _msg(receive_count: int = 1) -> TributeRenderMessage:
    return TributeRenderMessage(
        job_id="j", tribute_id="t1", person_id="p1", composed_at="c1",
        receipt_handle="r", raw_body="{}", receive_count=receive_count)


def test_renders_and_completes():
    calls = {}
    ctx = SimpleNamespace(tribute_id="t1", person_id="p1")

    def load(tid, ca):
        calls["load"] = (tid, ca)
        return ctx

    def run_render(c):
        calls["render"] = c
        return (True, True)

    def complete(tid, pid, v, p):
        calls["complete"] = (tid, pid, v, p)

    res = process_one(_msg(), load_context=load, run_render=run_render,
                      mark_complete=complete)
    assert res == "ok"
    assert calls["load"] == ("t1", "c1")
    assert calls["render"] is ctx
    assert calls["complete"] == ("t1", "p1", True, True)


def test_skips_when_context_missing_or_stale():
    rendered = False

    def load(tid, ca):
        return None  # missing or superseded

    def run_render(c):
        nonlocal rendered
        rendered = True
        return (True, True)

    def complete(*a):
        raise AssertionError("must not complete when context is missing")

    res = process_one(_msg(), load_context=load, run_render=run_render,
                      mark_complete=complete)
    assert res == "skip"
    assert rendered is False


def test_failure_propagates_so_caller_does_not_ack():
    def load(tid, ca):
        return SimpleNamespace(tribute_id="t1", person_id="p1")

    def run_render(c):
        raise RuntimeError("gemini down")

    def complete(*a):
        raise AssertionError("must not complete on render failure")

    with pytest.raises(RuntimeError):
        process_one(_msg(), load_context=load, run_render=run_render,
                    mark_complete=complete)


def test_handle_failure_retries_before_threshold():
    failed, acked = [], []
    outcome = handle_failure(
        _msg(receive_count=1), RuntimeError("gemini down"),
        max_attempts=3, mark_failed=lambda *a: failed.append(a),
        ack=lambda h: acked.append(h))
    assert outcome == "retry"
    assert failed == [] and acked == []  # left unacked for SQS redrive


def test_handle_failure_marks_failed_when_exhausted():
    failed, acked = [], []
    outcome = handle_failure(
        _msg(receive_count=3), RuntimeError("rendered_at missing"),
        max_attempts=3, mark_failed=lambda tid, err: failed.append((tid, err)),
        ack=lambda h: acked.append(h))
    assert outcome == "failed"
    assert failed == [("t1", "RuntimeError: rendered_at missing")]
    assert acked == ["r"]  # acked so it doesn't cycle to the DLQ


def test_parse_message_reads_receive_count():
    msg = _parse_message({
        "Body": '{"tribute_id": "t1", "person_id": "p1"}',
        "ReceiptHandle": "r",
        "Attributes": {"ApproximateReceiveCount": "4"},
    })
    assert msg.receive_count == 4


def test_parse_message_defaults_receive_count():
    msg = _parse_message({
        "Body": '{"tribute_id": "t1"}',
        "ReceiptHandle": "r",
    })
    assert msg.receive_count == 1
