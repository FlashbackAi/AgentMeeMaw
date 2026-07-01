"""Unit tests for the Voyage wrapper - no network calls."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from flashback.workers.embedding.voyage_client import VoyageClient, VoyageError


@dataclass
class _FakeResult:
    embeddings: list[list[float]]


class _FakeVoyage:
    def __init__(self, vectors: list[list[float]] | None = None,
                 raise_with: Exception | None = None) -> None:
        self._vectors = vectors or []
        self._raise_with = raise_with
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, texts, model, input_type=None):
        self.calls.append((list(texts), model))
        if self._raise_with is not None:
            raise self._raise_with
        return _FakeResult(embeddings=self._vectors)


def _client_with(fake: _FakeVoyage) -> VoyageClient:
    return VoyageClient(api_key="unused", _client=fake)


# The client enforces 1024-dim vectors (invariant #3); stubs must match.
def _vec(fill: float) -> list[float]:
    return [fill] * 1024


def test_empty_input_skips_api_call() -> None:
    fake = _FakeVoyage()
    client = _client_with(fake)
    assert client.embed_batch([], model="voyage-3-large") == []
    assert fake.calls == []


def test_returns_vectors_in_order() -> None:
    v1, v2 = _vec(0.1), _vec(0.3)
    fake = _FakeVoyage(vectors=[v1, v2])
    client = _client_with(fake)
    out = client.embed_batch(["a", "b"], model="voyage-3-large")
    assert out == [v1, v2]
    assert fake.calls == [(["a", "b"], "voyage-3-large")]


def test_passes_model_through() -> None:
    fake = _FakeVoyage(vectors=[_vec(0.0)])
    client = _client_with(fake)
    client.embed_batch(["x"], model="voyage-3")
    assert fake.calls[0][1] == "voyage-3"


def test_sdk_exception_becomes_voyage_error() -> None:
    fake = _FakeVoyage(raise_with=RuntimeError("upstream 500"))
    client = _client_with(fake)
    with pytest.raises(VoyageError) as excinfo:
        client.embed_batch(["x"], model="voyage-3-large")
    assert "upstream 500" in str(excinfo.value)


def test_count_mismatch_becomes_voyage_error() -> None:
    fake = _FakeVoyage(vectors=[_vec(0.0)])  # only 1 (correctly-sized) vector
    client = _client_with(fake)
    with pytest.raises(VoyageError):
        client.embed_batch(["a", "b"], model="voyage-3-large")
