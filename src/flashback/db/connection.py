"""
Postgres connection pool factory.

We use psycopg v3 with the threaded ConnectionPool. The pool is
configured to register the pgvector adapter on every checked-out
connection so the worker can write Python lists of floats directly
into ``vector(1024)`` columns.

The pool is constructed once at process startup. A single embedding
worker process needs only a small pool (defaults to ``min_size=1,
max_size=4``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from psycopg_pool import AsyncConnectionPool, ConnectionPool

if TYPE_CHECKING:
    from psycopg import AsyncConnection, Connection


def _configure_connection(conn: "Connection") -> None:
    from pgvector.psycopg import register_vector

    register_vector(conn)


async def _configure_async_connection(conn: "AsyncConnection") -> None:
    from pgvector.psycopg import register_vector_async

    await register_vector_async(conn)


def make_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 4,
) -> ConnectionPool:
    """Build a psycopg pool with pgvector type registration on every connection."""
    return ConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        configure=_configure_connection,
        open=True,
    )


def make_async_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 4,
) -> AsyncConnectionPool:
    """
    Build an async psycopg pool for the HTTP service.

    The pool is constructed *unopened* — FastAPI's lifespan calls
    ``await pool.open()`` and ``await pool.close()``. Constructing
    with ``open=True`` would require a running event loop, which the
    factory itself shouldn't assume. The pgvector adapter is registered
    on every connection checkout via ``register_vector_async``.
    """
    return AsyncConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        configure=_configure_async_connection,
        open=False,
    )
