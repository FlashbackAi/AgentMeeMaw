from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import pytest_asyncio
from psycopg.types.json import Json

from flashback.db.connection import make_async_pool
from flashback.retrieval import RetrievalService

MODEL = "voyage-3-large"
VERSION = "2025-01-07"
DIM = 1024


def vector(first: float = 1.0, second: float = 0.0) -> list[float]:
    out = [0.0] * DIM
    out[0] = first
    out[1] = second
    return out


class FakeEmbedder:
    def __init__(self, value: list[float] | None = None) -> None:
        self.value = value if value is not None else vector()
        self.calls: list[str] = []

    async def embed(self, query: str) -> list[float] | None:
        self.calls.append(query)
        return self.value


@pytest_asyncio.fixture
async def async_db_pool(schema_applied: str):
    pool = make_async_pool(schema_applied, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM edges")
                await cur.execute("DELETE FROM moment_history")
                await cur.execute("DELETE FROM moments")
                await cur.execute("DELETE FROM entities")
                await cur.execute("DELETE FROM threads")
                await cur.execute("DELETE FROM traits")
                await cur.execute("DELETE FROM questions WHERE source <> 'coverage_tap'")
                await cur.execute("DELETE FROM persons")
                await conn.commit()
        await pool.close()


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def retrieval_service(async_db_pool, fake_embedder) -> RetrievalService:
    return RetrievalService(
        db_pool=async_db_pool,
        voyage_embedder=fake_embedder,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
        default_limit=10,
        max_limit=50,
    )


async def insert_person(pool, name: str = "Test Subject") -> UUID:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO persons (name) VALUES (%s) RETURNING id",
                (name,),
            )
            (person_id,) = await cur.fetchone()
            await conn.commit()
    return person_id


async def insert_moment(
    pool,
    person_id: UUID,
    *,
    title: str = "Moment",
    narrative: str = "A remembered moment",
    embedding: list[float] | None = None,
    model: str | None = MODEL,
    version: str | None = VERSION,
    status: str = "active",
    created_at: datetime | None = None,
) -> UUID:
    created_at = created_at or datetime.now(timezone.utc)
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            if embedding is None:
                await cur.execute(
                    """
                    INSERT INTO moments
                        (person_id, title, narrative, status, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (person_id, title, narrative, status, created_at),
                )
            else:
                await cur.execute(
                    """
                    INSERT INTO moments
                        (person_id, title, narrative, status, narrative_embedding,
                         embedding_model, embedding_model_version, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        person_id,
                        title,
                        narrative,
                        status,
                        embedding,
                        model,
                        version,
                        created_at,
                    ),
                )
            (moment_id,) = await cur.fetchone()
            await conn.commit()
    return moment_id


async def insert_entity(
    pool,
    person_id: UUID,
    *,
    kind: str = "person",
    name: str = "Entity",
    description: str | None = "description",
    status: str = "active",
    attributes: dict | None = None,
    created_at: datetime | None = None,
    embedding: list[float] | None = None,
    model: str | None = MODEL,
    version: str | None = VERSION,
) -> UUID:
    created_at = created_at or datetime.now(timezone.utc)
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            if embedding is None:
                await cur.execute(
                    """
                    INSERT INTO entities
                        (person_id, kind, name, description, aliases, attributes,
                         status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        person_id,
                        kind,
                        name,
                        description,
                        ["alias"],
                        Json(attributes or {}),
                        status,
                        created_at,
                    ),
                )
            else:
                await cur.execute(
                    """
                    INSERT INTO entities
                        (person_id, kind, name, description, aliases, attributes,
                         status, created_at, description_embedding,
                         embedding_model, embedding_model_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        person_id,
                        kind,
                        name,
                        description,
                        ["alias"],
                        Json(attributes or {}),
                        status,
                        created_at,
                        embedding,
                        model,
                        version,
                    ),
                )
            (entity_id,) = await cur.fetchone()
            await conn.commit()
    return entity_id


async def insert_thread(
    pool,
    person_id: UUID,
    *,
    name: str = "Thread",
    description: str = "description",
    source: str = "auto-detected",
    confidence: float | None = 0.8,
    status: str = "active",
    created_at: datetime | None = None,
) -> UUID:
    created_at = created_at or datetime.now(timezone.utc)
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO threads
                    (person_id, name, description, source, confidence, status,
                     created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    person_id,
                    name,
                    description,
                    source,
                    confidence,
                    status,
                    created_at,
                ),
            )
            (thread_id,) = await cur.fetchone()
            await conn.commit()
    return thread_id


async def insert_question(
    pool,
    person_id: UUID,
    *,
    text: str = "Who was this?",
    source: str = "dropped_reference",
    status: str = "active",
    attributes: dict | None = None,
    created_at: datetime | None = None,
) -> UUID:
    created_at = created_at or datetime.now(timezone.utc)
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO questions
                    (person_id, text, source, attributes, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    person_id,
                    text,
                    source,
                    Json(attributes or {"dropped_phrase": "the porch", "themes": []}),
                    status,
                    created_at,
                ),
            )
            (question_id,) = await cur.fetchone()
            await conn.commit()
    return question_id


async def insert_edge(
    pool,
    *,
    from_kind: str,
    from_id: UUID,
    to_kind: str,
    to_id: UUID,
    edge_type: str,
    status: str = "active",
) -> UUID:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO edges
                    (from_kind, from_id, to_kind, to_id, edge_type, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (from_kind, from_id, to_kind, to_id, edge_type, status),
            )
            (edge_id,) = await cur.fetchone()
            await conn.commit()
    return edge_id


def recent(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)
