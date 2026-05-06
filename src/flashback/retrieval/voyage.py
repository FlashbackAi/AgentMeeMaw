"""Query embedding wrapper for retrieval-time Voyage calls."""

from __future__ import annotations

import asyncio
from typing import Protocol

import structlog
import voyageai

log = structlog.get_logger("flashback.retrieval.voyage")
EXPECTED_EMBEDDING_DIM = 1024


class _VoyageLike(Protocol):
    def embed(
        self, texts: list[str], model: str, input_type: str | None = None
    ): ...


class VoyageQueryEmbedder:
    """Synchronous Voyage query embedding wrapped for async callers.

    Retrieval is best-effort on the turn hot path: timeout or SDK
    failures return ``None`` and callers continue without graph context.
    """

    def __init__(
        self,
        voyage_client: _VoyageLike,
        model: str,
        timeout: float,
    ) -> None:
        self._client = voyage_client
        self._model = model
        self._timeout = timeout

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        model: str,
        timeout: float,
    ) -> "VoyageQueryEmbedder":
        return cls(
            voyage_client=voyageai.Client(api_key=api_key),
            model=model,
            timeout=timeout,
        )

    async def embed(self, query: str) -> list[float] | None:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._embed_sync, query),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            log.warning("voyage_query_embedding.timed_out", timeout=self._timeout)
            return None
        except Exception as exc:
            log.warning("voyage_query_embedding.failed", error=str(exc))
            return None

    def _embed_sync(self, query: str) -> list[float]:
        result = self._client.embed(
            [query],
            model=self._model,
            input_type="query",
        )
        vector = list(result.embeddings[0])
        if len(vector) != EXPECTED_EMBEDDING_DIM:
            raise ValueError(
                f"Voyage returned dim={len(vector)}; expected {EXPECTED_EMBEDDING_DIM}"
            )
        return vector
