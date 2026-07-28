from types import SimpleNamespace

import pytest

import flashback.llm.interface as interface


class _FakeAnthropicMessage:
    def __init__(self):
        self.stop_reason = "tool_use"
        self.usage = SimpleNamespace(
            input_tokens=120, output_tokens=40,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )
        self.content = [SimpleNamespace(type="tool_use", name="t", input={"ok": True})]


@pytest.mark.asyncio
async def test_call_with_tool_records_usage(monkeypatch):
    captured = {}

    async def _fake_record(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(interface.usage_recorder, "record_llm_usage", _fake_record)
    monkeypatch.setattr(interface, "get_anthropic_client", lambda s: object())

    async def _fake_retries(factory, *, provider, settings):
        return _FakeAnthropicMessage()

    monkeypatch.setattr(interface, "_with_provider_retries", _fake_retries)

    tool = SimpleNamespace(name="t", description="d", input_schema={"type": "object"})
    result = await interface.call_with_tool(
        provider="anthropic", model="claude-sonnet-4-6",
        system_prompt="s", user_message="u", tool=tool,
        max_tokens=100, timeout=5.0, settings=SimpleNamespace(),
        feature="response_generate",
    )
    assert result == {"ok": True}
    assert captured["feature"] == "response_generate"
    assert captured["provider"] == "anthropic"
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["input_tokens"] == 120
    assert captured["output_tokens"] == 40


@pytest.mark.asyncio
async def test_call_text_stream_records_usage_from_final_message(monkeypatch):
    captured = {}

    async def _fake_record(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(interface.usage_recorder, "record_llm_usage", _fake_record)

    class _FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        @property
        def text_stream(self):
            async def _gen():
                yield "hello"
            return _gen()

        async def get_final_message(self):
            return SimpleNamespace(usage=SimpleNamespace(
                input_tokens=10, output_tokens=5,
                cache_read_input_tokens=0, cache_creation_input_tokens=0))

    monkeypatch.setattr(interface, "get_anthropic_client", lambda s: SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: _FakeStream())))

    chunks = []
    async for c in interface.call_text_stream(
        provider="anthropic", model="claude-sonnet-4-6",
        system_prompt="s", user_message="u",
        max_tokens=100, timeout=5.0, settings=SimpleNamespace(),
        feature="response_generate",
    ):
        chunks.append(c)
    assert chunks == ["hello"]
    assert captured["input_tokens"] == 10 and captured["output_tokens"] == 5
    assert captured["feature"] == "response_generate"
