"""
Thin wrapper around the Voyage AI SDK for batch embedding calls.

The wrapper exists for two reasons:

1. The worker batches all messages from a single SQS receive into one
   Voyage call (per (model, version) group). Centralising this lets us
   tune batching without touching the worker loop.
2. Tests inject a fake client with the same signature, avoiding any
   real network calls in unit tests.

Voyage errors propagate as :class:`VoyageError`. The worker treats
that as "do not ack" so SQS will redeliver after the visibility
timeout - meaning a transient Voyage outage just means delayed
embeddings, never lost ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import voyageai


class VoyageError(RuntimeError):
    """Raised when the Voyage API call fails or returns malformed output."""


class _VoyageLike(Protocol):
    def embed(
        self, texts: list[str], model: str, input_type: str | None = None
    ): ...


@dataclass
class VoyageClient:
    """
    Wraps :class:`voyageai.Client` so the worker has a stable surface
    and tests can substitute a stub implementing the same ``embed``
    method.
    """

    api_key: str
    _client: _VoyageLike | None = None

    def _get_client(self) -> _VoyageLike:
        if self._client is None:
            self._client = voyageai.Client(api_key=self.api_key)
        return self._client

    def embed_batch(self, texts: list[str], model: str) -> list[list[float]]:
        """
        Embed a batch of texts. Returns vectors in input order.

        Raises :class:`VoyageError` on any SDK error or shape mismatch.
        """
        if not texts:
            return []
        client = self._get_client()
        try:
            result = client.embed(texts, model=model, input_type="document")
        except Exception as exc:
            raise VoyageError(
                f"Voyage embed call failed (model={model}, batch={len(texts)}): {exc}"
            ) from exc

        embeddings = getattr(result, "embeddings", None)
        if embeddings is None or len(embeddings) != len(texts):
            raise VoyageError(
                f"Voyage returned {len(embeddings) if embeddings is not None else 'no'} "
                f"embeddings for {len(texts)} inputs"
            )
        return [list(vec) for vec in embeddings]
