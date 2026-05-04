from __future__ import annotations

import time
from dataclasses import dataclass

from flashback.retrieval.voyage import VoyageQueryEmbedder


@dataclass
class _Result:
    embeddings: list[list[float]]


class _FakeVoyage:
    def __init__(
        self,
        *,
        embeddings: list[list[float]] | None = None,
        raise_with: Exception | None = None,
        sleep_for: float = 0.0,
    ) -> None:
        self.embeddings = embeddings or [[0.1, 0.2]]
        self.raise_with = raise_with
        self.sleep_for = sleep_for
        self.calls = []

    def embed(self, texts, model, input_type=None):
        self.calls.append({"texts": list(texts), "model": model, "input_type": input_type})
        if self.sleep_for:
            time.sleep(self.sleep_for)
        if self.raise_with is not None:
            raise self.raise_with
        return _Result(self.embeddings)


async def test_happy_path_returns_vector() -> None:
    fake = _FakeVoyage(embeddings=[[0.3, 0.4]])
    embedder = VoyageQueryEmbedder(fake, model="voyage-3-large", timeout=1)

    assert await embedder.embed("porch") == [0.3, 0.4]
    assert fake.calls == [
        {"texts": ["porch"], "model": "voyage-3-large", "input_type": "query"}
    ]


async def test_timeout_returns_none() -> None:
    fake = _FakeVoyage(sleep_for=0.05)
    embedder = VoyageQueryEmbedder(fake, model="voyage-3-large", timeout=0.001)

    assert await embedder.embed("porch") is None


async def test_generic_exception_returns_none() -> None:
    fake = _FakeVoyage(raise_with=RuntimeError("upstream gone"))
    embedder = VoyageQueryEmbedder(fake, model="voyage-3-large", timeout=1)

    assert await embedder.embed("porch") is None


async def test_passes_input_type_query_to_sdk() -> None:
    fake = _FakeVoyage()
    embedder = VoyageQueryEmbedder(fake, model="voyage-3-large", timeout=1)

    await embedder.embed("where was the old shop?")

    assert fake.calls[0]["input_type"] == "query"
