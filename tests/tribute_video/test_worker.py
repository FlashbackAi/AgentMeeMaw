"""Unit tests for the tribute_render worker orchestration (deps mocked)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flashback.tribute_video.context import RenderContext, build_context_dict
from flashback.workers.tribute_render.sqs_client import (
    TributeRenderMessage,
    _parse_message,
)
from flashback.workers.tribute_render import worker as worker_mod
from flashback.workers.tribute_render.worker import (
    assemble_book,
    handle_failure,
    process_one,
)


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
        return (True, True, True)

    def complete(tid, pid, v, p, poster):
        calls["complete"] = (tid, pid, v, p, poster)

    res = process_one(_msg(), load_context=load, run_render=run_render,
                      mark_complete=complete)
    assert res == "ok"
    assert calls["load"] == ("t1", "c1")
    assert calls["render"] is ctx
    assert calls["complete"] == ("t1", "p1", True, True, True)


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


def test_context_round_trip_carries_assembly_inputs():
    d = build_context_dict(
        subject_name="Dad", relationship="father", gt_context="south india",
        candidates=[{"id": "m1", "title": "t", "narrative": "n"}],
        video_put_url="https://put/v", pdf_put_url="https://put/p",
        message_text="thanks", archetype_leads=["a lead"], n_pages=12,
        composed_at="2026-06-20T00:00:00Z",
    )
    assert "book" not in d  # inputs, not a pre-built Book
    ctx = RenderContext.from_dict(d, tribute_id="t1", person_id="p1")
    assert ctx.candidates == [{"id": "m1", "title": "t", "narrative": "n"}]
    assert ctx.message_text == "thanks"
    assert ctx.archetype_leads == ["a lead"]
    assert ctx.n_pages == 12
    assert ctx.video_put_url == "https://put/v"


def _render_ctx(**over) -> RenderContext:
    base = dict(
        tribute_id="t1", person_id="p1", subject_name="Dad",
        relationship="father", gt_context="", video_put_url="v",
        pdf_put_url="p", candidates=[{"id": "m1"}])
    base.update(over)
    return RenderContext(**base)


def _stub_render(monkeypatch):
    uploaded = []
    monkeypatch.setattr(worker_mod, "assemble_book",
                        lambda ctx, *, settings: SimpleNamespace(beats=[]))
    monkeypatch.setattr(worker_mod, "render_book", lambda **kw: None)
    monkeypatch.setattr(worker_mod.transfer, "download_image",
                        lambda url, **k: None)

    def fake_upload(url, path, *, content_type, timeout=180.0):
        uploaded.append((url, content_type))
        return 200

    monkeypatch.setattr(worker_mod.transfer, "upload_file", fake_upload)
    return uploaded


def test_render_and_upload_uploads_poster_when_url_present(monkeypatch):
    uploaded = _stub_render(monkeypatch)
    ctx = _render_ctx(poster_put_url="poster-url")
    video, pdf, poster = worker_mod.render_and_upload(
        ctx, artist=None, tmpdir="/tmp", settings=SimpleNamespace())
    assert (video, pdf, poster) == (True, True, True)
    assert ("poster-url", "image/jpeg") in uploaded


def test_render_and_upload_skips_poster_without_url(monkeypatch):
    uploaded = _stub_render(monkeypatch)
    ctx = _render_ctx()  # no poster_put_url
    video, pdf, poster = worker_mod.render_and_upload(
        ctx, artist=None, tmpdir="/tmp", settings=SimpleNamespace())
    assert (video, pdf, poster) == (True, True, False)
    assert all(ct != "image/jpeg" for _url, ct in uploaded)


def test_assemble_book_passes_context_inputs(monkeypatch):
    from flashback.tribute_video.book import Beat, Book

    seen = {}

    async def _fake_assemble(**kwargs):
        seen.update(kwargs)
        return Book(cover_title="C", opener=Beat(line="o", art_direction=""),
                    beats=[Beat(line="b", art_direction="", moment_id="m1")],
                    closing=Beat(line="c", art_direction=""), message="thanks")

    monkeypatch.setattr(worker_mod, "assemble_storybook_video", _fake_assemble)
    ctx = RenderContext(
        tribute_id="t1", person_id="p1", subject_name="Dad",
        relationship="father", gt_context="gt", video_put_url="v",
        pdf_put_url="p", candidates=[{"id": "m1"}], message_text="thanks",
        archetype_leads=["lead"], n_pages=9, gender="he")

    book = assemble_book(ctx, settings=SimpleNamespace())

    assert book.beats[0].moment_id == "m1"
    assert seen["candidates"] == [{"id": "m1"}]
    assert seen["message_text"] == "thanks"
    assert seen["n_pages"] == 9
    assert seen["subject_name"] == "Dad"
    assert seen["subject_gender"] == "he"


def test_render_and_upload_passes_subject_gender_to_render_book(monkeypatch):
    uploaded = _stub_render(monkeypatch)
    seen = {}

    def fake_render_book(**kw):
        seen.update(kw)

    monkeypatch.setattr(worker_mod, "render_book", fake_render_book)
    ctx = _render_ctx(gender="she")
    worker_mod.render_and_upload(
        ctx, artist=None, tmpdir="/tmp", settings=SimpleNamespace())
    assert seen["subject_gender"] == "she"
    assert uploaded  # sanity: render still completed
