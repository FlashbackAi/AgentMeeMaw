"""storybook_render worker — orchestration seams (all callables injected)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from flashback.workers.storybook_render.worker import (
    handle_failure,
    process_one,
    select_moments,
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


def test_select_moments_grid_uses_curated_slice() -> None:
    ctx = SimpleNamespace(
        collection="childhood",
        moments=[{"i": 0}, {"i": 1}, {"i": 2}],
    )
    out = select_moments(ctx, {"childhood": [2, 0]})
    assert out == [{"i": 2}, {"i": 0}]


def test_select_moments_grid_empty_curation_falls_back_to_pool() -> None:
    ctx = SimpleNamespace(collection="childhood", moments=[{"i": 0}])
    assert select_moments(ctx, {"childhood": []}) == [{"i": 0}]


def test_select_moments_chapter_lenses_whole_pool() -> None:
    ctx = SimpleNamespace(
        collection="wisdom", moments=[{"i": 0}, {"i": 1}]
    )
    assert select_moments(ctx, {}) == ctx.moments
