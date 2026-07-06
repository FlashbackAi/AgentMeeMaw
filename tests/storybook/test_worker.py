"""storybook_render worker — orchestration seams (all callables injected)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from flashback.storybook.context import StorybookRenderContext
from flashback.workers.storybook_render import worker
from flashback.workers.storybook_render.worker import (
    handle_failure,
    process_one,
)


def _msg(count: int = 1):
    return SimpleNamespace(
        storybook_id="s",
        composed_at="t",
        receipt_handle="rh",
        receive_count=count,
    )


def test_skip_when_context_missing() -> None:
    out = process_one(
        _msg(),
        load_context=lambda *a: None,
        run_render=MagicMock(),
        mark_complete=MagicMock(),
    )
    assert out == "skip"


def test_ok_marks_complete_with_counts() -> None:
    ctx = SimpleNamespace(
        storybook_id="s", person_id="p", collection="childhood"
    )
    mc = MagicMock()
    out = process_one(
        _msg(),
        load_context=lambda *a: ctx,
        run_render=lambda c: (True, 7, True),
        mark_complete=mc,
    )
    assert out == "ok"
    mc.assert_called_once_with("s", "p", "childhood", True, 7, True)


def test_render_exception_propagates_for_redrive() -> None:
    ctx = SimpleNamespace(storybook_id="s", person_id="p", collection="c")

    def boom(_ctx):
        raise RuntimeError("render died")

    try:
        process_one(
            _msg(),
            load_context=lambda *a: ctx,
            run_render=boom,
            mark_complete=MagicMock(),
        )
        raise AssertionError("should have raised")
    except RuntimeError:
        pass


def test_handle_failure_retries_then_fails() -> None:
    mf, ack = MagicMock(), MagicMock()
    assert (
        handle_failure(
            _msg(1), RuntimeError("x"), max_attempts=3,
            mark_failed=mf, ack=ack,
        )
        == "retry"
    )
    mf.assert_not_called()
    assert (
        handle_failure(
            _msg(3), RuntimeError("x"), max_attempts=3,
            mark_failed=mf, ack=ack,
        )
        == "failed"
    )
    mf.assert_called_once()
    ack.assert_called_once_with("rh")


# --- deterministic assembly (design 2026-07-06 — curation retired) -----------


def _ctx(collection: str, *, user_curated: bool = False) -> StorybookRenderContext:
    return StorybookRenderContext(
        storybook_id="sb1", person_id="p1", collection=collection,
        subject_name="Dad", relationship="father", gt_context="",
        pdf_put_url="u", cover_put_url="u", page_put_urls=["u"] * 7,
        moments=[{"id": f"m-{i}", "title": f"t{i}", "narrative": "n"}
                 for i in range(6)],
        user_curated=user_curated,
    )


async def test_assemble_uses_context_slice_verbatim(monkeypatch) -> None:
    """The worker assembles from ctx.moments directly — no curation, no
    fallback to an unrelated pool (that judgement moved to the route)."""
    captured: dict = {}

    async def _fake_assemble(**kwargs):
        captured.update(kwargs)
        return "SCRIPT"

    monkeypatch.setattr(worker, "assemble_script", _fake_assemble)
    ctx = _ctx("childhood")
    got = await worker._assemble(ctx, settings=object())
    assert got == "SCRIPT"
    assert captured["moments"] == ctx.moments
    assert captured["collection"].slug == "childhood"


def test_worker_has_no_curation_surface() -> None:
    """Curation is retired; the worker must not re-expose a content chooser."""
    assert not hasattr(worker, "select_moments")
    assert not hasattr(worker, "curate_moments")
    assert not hasattr(worker, "_curate_and_assemble")
