"""
Sync Voyage query embedder for the Extraction Worker.

The HTTP service has an async :class:`flashback.retrieval.voyage.VoyageQueryEmbedder`.
The extraction worker is a sync process and does its similarity search
inside a sync function, so we ship a parallel sync wrapper here.

Critical reading of CLAUDE.md invariant #4 ("never generate embeddings
inline"): #4 governs **stored** embeddings. Stored vectors must always
flow through the embedding queue so the model identity stamping stays
in lockstep. Query embedding for similarity search is the same pattern
step 6 (Retrieval Service) established — the vector is consumed
in-process and discarded; nothing is written to a vector column.

Failures (timeout, SDK error) return ``None``; callers degrade
gracefully and skip refinement detection for that moment. Better to
miss a refinement than to break extraction on a Voyage outage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import structlog
import voyageai

log = structlog.get_logger("flashback.workers.extraction.voyage_query")


class _VoyageLike(Protocol):
    def embed(
        self, texts: list[str], model: str, input_type: str | None = None
    ): ...


@dataclass
class SyncVoyageQueryEmbedder:
    """Synchronous Voyage query embedder.

    Mirrors :class:`flashback.retrieval.voyage.VoyageQueryEmbedder` but
    without the asyncio.to_thread/wait_for layer.
    """

    model: str
    timeout: float
    api_key: str | None = None
    _client: _VoyageLike | None = None

    def _get_client(self) -> _VoyageLike:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "SyncVoyageQueryEmbedder needs api_key or an injected client"
                )
            self._client = voyageai.Client(api_key=self.api_key)
        return self._client

    def embed(self, query: str) -> list[float] | None:
        try:
            result = self._get_client().embed(
                [query],
                model=self.model,
                input_type="query",
            )
        except Exception as exc:
            log.warning("voyage_query_embedding.failed", error=str(exc))
            return None

        embeddings = getattr(result, "embeddings", None)
        if not embeddings:
            log.warning("voyage_query_embedding.empty_response")
            return None
        return list(embeddings[0])
