"""
Dependency-injection wiring for the FastAPI app.

The HTTP service uses three long-lived singletons:

* :class:`HttpConfig` — read from environment variables once at startup.
* :class:`AsyncConnectionPool` — psycopg pool for Postgres reads.
* Async ``Redis`` — single connection-pooled client for Valkey.

These live on ``app.state`` and are exposed to handlers via the
``Depends(get_*)`` functions defined here. Tests override the
dependencies with ``app.dependency_overrides`` (see
:mod:`tests.http.conftest`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

from flashback.config import HttpConfig
from flashback.orchestrator import Orchestrator
from flashback.working_memory import WorkingMemory

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool
    from redis.asyncio import Redis


def get_http_config(request: Request) -> HttpConfig:
    return request.app.state.http_config


def get_db_pool(request: Request) -> "AsyncConnectionPool":
    return request.app.state.db_pool


def get_redis(request: Request) -> "Redis":
    return request.app.state.redis


def get_working_memory(request: Request) -> WorkingMemory:
    return request.app.state.working_memory


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator
