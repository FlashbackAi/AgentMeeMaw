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

from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    from psycopg import Connection


def _configure_connection(conn: "Connection") -> None:
    from pgvector.psycopg import register_vector

    register_vector(conn)


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
