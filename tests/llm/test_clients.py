from __future__ import annotations

from types import SimpleNamespace

from flashback.llm import clients


def test_get_anthropic_client_reuses_instance(monkeypatch):
    constructed = []

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            constructed.append(self)

    monkeypatch.setattr(clients, "_anthropic", None)
    monkeypatch.setattr(clients, "AsyncAnthropic", FakeAnthropic)
    settings = SimpleNamespace(anthropic_api_key="anthropic-key")

    first = clients.get_anthropic_client(settings)
    second = clients.get_anthropic_client(settings)

    assert first is second
    assert len(constructed) == 1
    assert first.kwargs == {"api_key": "anthropic-key", "max_retries": 1}


def test_get_openai_client_reuses_instance(monkeypatch):
    constructed = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            constructed.append(self)

    monkeypatch.setattr(clients, "_openai", None)
    monkeypatch.setattr(clients, "AsyncOpenAI", FakeOpenAI)
    settings = SimpleNamespace(openai_api_key="openai-key")

    first = clients.get_openai_client(settings)
    second = clients.get_openai_client(settings)

    assert first is second
    assert len(constructed) == 1
    assert first.kwargs == {"api_key": "openai-key", "max_retries": 1}
