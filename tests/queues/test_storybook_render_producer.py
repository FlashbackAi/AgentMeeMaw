"""storybook_render queue producer — trigger-only payload."""

from __future__ import annotations

from unittest.mock import AsyncMock

from flashback.queues.storybook_render import StorybookRenderQueueProducer


async def test_no_queue_url_returns_none() -> None:
    p = StorybookRenderQueueProducer(AsyncMock(), "")
    out = await p.push(
        job_id="j", storybook_id="s", person_id="p", composed_at="t"
    )
    assert out is None


async def test_payload_is_trigger_only() -> None:
    sqs = AsyncMock()
    sqs.send_message.return_value = "mid"
    p = StorybookRenderQueueProducer(sqs, "http://q")
    out = await p.push(
        job_id="j", storybook_id="s", person_id="p", composed_at="t"
    )
    assert out == "mid"
    url, payload = sqs.send_message.call_args.args
    assert url == "http://q"
    assert set(payload) == {
        "job_id",
        "storybook_id",
        "person_id",
        "composed_at",
        "enqueued_at",
    }
    assert payload["storybook_id"] == "s"
    assert payload["composed_at"] == "t"
