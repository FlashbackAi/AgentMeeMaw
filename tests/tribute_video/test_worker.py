"""Unit tests for the tribute_render worker orchestration (deps mocked)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flashback.workers.tribute_render.sqs_client import TributeRenderMessage
from flashback.workers.tribute_render.worker import process_one


def _msg() -> TributeRenderMessage:
    return TributeRenderMessage(
        job_id="j", tribute_id="t1", person_id="p1", composed_at="c1",
        receipt_handle="r", raw_body="{}")


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
