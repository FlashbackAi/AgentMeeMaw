"""Attribution via the ambient usage context (Phase 21 per-user cost)."""

import asyncio

from flashback.usage import pricing, recorder
from flashback.usage.context import bind_usage_context, current_usage_context


def _capture(monkeypatch) -> dict:
    captured: dict = {}
    monkeypatch.setattr(
        recorder, "insert_event", lambda row: captured.update(row) or "id"
    )
    return captured


def test_llm_row_inherits_bound_person_and_session(monkeypatch):
    captured = _capture(monkeypatch)
    with bind_usage_context(person_id="p-1", session_id="s-1"):
        recorder.record_llm_usage_sync(
            feature="extraction", provider="anthropic", model="claude-sonnet-4-6",
            input_tokens=10, output_tokens=5,
        )
    assert captured["person_id"] == "p-1"
    assert captured["session_id"] == "s-1"


def test_image_row_inherits_bound_person(monkeypatch):
    monkeypatch.setitem(pricing.IMAGE_PRICING, ("gemini", "img-x"), 0.04)
    captured = _capture(monkeypatch)
    with bind_usage_context(person_id="p-2"):
        recorder.record_image_usage_sync(
            feature="storybook_image", provider="gemini", model="img-x",
        )
    assert captured["person_id"] == "p-2"
    assert captured["session_id"] is None


def test_explicit_argument_wins_over_binding(monkeypatch):
    captured = _capture(monkeypatch)
    with bind_usage_context(person_id="ambient"):
        recorder.record_llm_usage_sync(
            feature="extraction", provider="anthropic", model="claude-sonnet-4-6",
            input_tokens=1, output_tokens=1, person_id="explicit",
        )
    assert captured["person_id"] == "explicit"


def test_no_binding_leaves_attribution_null(monkeypatch):
    captured = _capture(monkeypatch)
    recorder.record_llm_usage_sync(
        feature="extraction", provider="anthropic", model="claude-sonnet-4-6",
        input_tokens=1, output_tokens=1,
    )
    assert captured["person_id"] is None
    assert captured["session_id"] is None


def test_uuid_person_id_is_coerced_to_str(monkeypatch):
    import uuid

    captured = _capture(monkeypatch)
    pid = uuid.uuid4()
    with bind_usage_context(person_id=pid):
        recorder.record_llm_usage_sync(
            feature="response_generate", provider="anthropic",
            model="claude-sonnet-4-6", input_tokens=1, output_tokens=1,
        )
    assert captured["person_id"] == str(pid)


def test_nested_bind_is_additive_not_clobbering():
    with bind_usage_context(person_id="p-outer"):
        with bind_usage_context(session_id="s-inner"):
            ctx = current_usage_context()
            assert ctx.person_id == "p-outer"  # not wiped by inner bind
            assert ctx.session_id == "s-inner"
        # inner scope restored on exit
        assert current_usage_context().session_id is None
    assert current_usage_context().person_id is None


def test_binding_propagates_across_to_thread(monkeypatch):
    """The async recorder runs its insert via asyncio.to_thread; the binding
    on the event loop must reach the sync insert in the worker thread."""
    captured = _capture(monkeypatch)

    async def _run():
        with bind_usage_context(person_id="p-thread", session_id="s-thread"):
            await recorder.record_llm_usage(
                feature="response_generate", provider="anthropic",
                model="claude-sonnet-4-6", input_tokens=2, output_tokens=2,
            )

    asyncio.run(_run())
    assert captured["person_id"] == "p-thread"
    assert captured["session_id"] == "s-thread"
