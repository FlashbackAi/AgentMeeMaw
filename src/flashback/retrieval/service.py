"""Read-only accessors over the canonical memory graph."""

from __future__ import annotations

from uuid import UUID

from pgvector.psycopg import Vector
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from flashback.retrieval.queries import (
    GET_DROPPED_PHRASES_SQL,
    GET_ENTITIES_BY_IDS_SQL,
    GET_ENTITIES_BY_KIND_SQL,
    GET_ENTITIES_SQL,
    GET_RELATED_MOMENTS_SQL,
    GET_SESSION_SUMMARY_SQL,
    GET_THREADS_FOR_ENTITY_SQL,
    GET_THREADS_SQL,
    SEARCH_ENTITIES_SQL,
    SEARCH_MOMENTS_SQL,
)
from flashback.retrieval.schema import (
    DroppedPhraseResult,
    EntityResult,
    MomentResult,
    SessionSummaryResult,
    ThreadResult,
)
from flashback.retrieval.voyage import VoyageQueryEmbedder


class RetrievalService:
    """Read-only access to the canonical graph for the agent.

    Every graph query goes through ``active_*`` views, every graph result
    is scoped by ``person_id``, and vector search filters by embedding
    model identity.
    """

    def __init__(
        self,
        db_pool: AsyncConnectionPool,
        voyage_embedder: VoyageQueryEmbedder,
        embedding_model: str,
        embedding_model_version: str,
        default_limit: int,
        max_limit: int,
    ) -> None:
        self._pool = db_pool
        self._embedder = voyage_embedder
        self._embedding_model = embedding_model
        self._embedding_model_version = embedding_model_version
        self._default_limit = default_limit
        self._max_limit = max_limit

    async def embed_query(self, text: str) -> list[float] | None:
        """Embed a query string for similarity search."""
        return await self._embedder.embed(text)

    async def search_moments(
        self,
        query: str,
        person_id: UUID,
        limit: int | None = None,
    ) -> list[MomentResult]:
        """Vector similarity search over active moments for a person."""
        clamped_limit = self._clamp_limit(limit)
        vector = await self.embed_query(query)
        if vector is None:
            return []

        rows = await self._fetch_all(
            SEARCH_MOMENTS_SQL,
            {
                "person_id": person_id,
                "query_vector": Vector(vector),
                "embedding_model": self._embedding_model,
                "embedding_model_version": self._embedding_model_version,
                "limit": clamped_limit,
            },
        )
        return [MomentResult.model_validate(row) for row in rows]

    async def search_entities(
        self,
        query: str,
        person_id: UUID,
        limit: int | None = None,
    ) -> list[EntityResult]:
        """Vector similarity search over active entities for a person."""
        clamped_limit = self._clamp_limit(limit)
        vector = await self.embed_query(query)
        if vector is None:
            return []

        rows = await self._fetch_all(
            SEARCH_ENTITIES_SQL,
            {
                "person_id": person_id,
                "query_vector": Vector(vector),
                "embedding_model": self._embedding_model,
                "embedding_model_version": self._embedding_model_version,
                "limit": clamped_limit,
            },
        )
        return [EntityResult.model_validate(row) for row in rows]

    async def get_entities_by_ids(
        self,
        person_id: UUID,
        entity_ids: list[UUID],
    ) -> list[EntityResult]:
        """Fetch active entities by id, scoped to a person.

        Used by the deterministic entity-mention scanner, which already
        knows the entity ids it wants (from the cached name list) and
        needs the full descriptions for the response generator's
        context. No vector search; no embedding call.
        """
        if not entity_ids:
            return []
        rows = await self._fetch_all(
            GET_ENTITIES_BY_IDS_SQL,
            {"person_id": person_id, "entity_ids": entity_ids},
        )
        return [EntityResult.model_validate(row) for row in rows]

    async def get_entities(
        self,
        person_id: UUID,
        kind: str | None = None,
    ) -> list[EntityResult]:
        """Return active entities for a person, optionally filtered by kind."""
        if kind is None:
            rows = await self._fetch_all(GET_ENTITIES_SQL, {"person_id": person_id})
        else:
            rows = await self._fetch_all(
                GET_ENTITIES_BY_KIND_SQL,
                {"person_id": person_id, "kind": kind},
            )
        return [EntityResult.model_validate(row) for row in rows]

    async def get_related_moments(
        self,
        entity_id: UUID,
        person_id: UUID,
        limit: int | None = None,
    ) -> list[MomentResult]:
        """Return active moments linked to an active entity by ``involves``."""
        rows = await self._fetch_all(
            GET_RELATED_MOMENTS_SQL,
            {
                "entity_id": entity_id,
                "person_id": person_id,
                "limit": self._clamp_limit(limit),
            },
        )
        return [MomentResult.model_validate(row) for row in rows]

    async def get_threads(self, person_id: UUID) -> list[ThreadResult]:
        """Return active threads for a person."""
        rows = await self._fetch_all(GET_THREADS_SQL, {"person_id": person_id})
        return [ThreadResult.model_validate(row) for row in rows]

    async def get_threads_for_entity(
        self,
        entity_id: UUID,
        person_id: UUID,
    ) -> list[ThreadResult]:
        """Return active threads an active entity evidences."""
        rows = await self._fetch_all(
            GET_THREADS_FOR_ENTITY_SQL,
            {"entity_id": entity_id, "person_id": person_id},
        )
        return [ThreadResult.model_validate(row) for row in rows]

    async def get_threads_summary(self, person_id: UUID) -> list[ThreadResult]:
        """For v1, the summary surface is the thread list itself."""
        return await self.get_threads(person_id)

    async def get_dropped_phrases_for_session(
        self,
        session_id: UUID,
        person_id: UUID,
    ) -> list[DroppedPhraseResult]:
        """Return open dropped-reference questions for the person.

        ``session_id`` becomes meaningful once step 11 writes
        ``motivated_by`` edges; step 6 deliberately scopes by person.
        """
        _ = session_id
        rows = await self._fetch_all(
            GET_DROPPED_PHRASES_SQL,
            {"person_id": person_id},
        )
        return [DroppedPhraseResult.model_validate(row) for row in rows]

    async def get_session_summary(
        self,
        session_id: UUID,
    ) -> SessionSummaryResult | None:
        """Return ``None`` until Session Wrap persists summaries in step 18."""
        _ = (session_id, GET_SESSION_SUMMARY_SQL)
        return None

    def _clamp_limit(self, limit: int | None) -> int:
        if limit is None:
            return self._default_limit
        return max(1, min(limit, self._max_limit))

    async def _fetch_all(self, sql: str, params: dict) -> list[dict]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        return list(rows)
