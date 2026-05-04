"""Shared fixtures for Working Memory tests.

Backs the redis-py async client with fakeredis so the test suite has
zero external service dependencies. fakeredis[lua] provides EVAL
support for the rolling-summary script.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest_asyncio

from flashback.working_memory.client import WorkingMemory


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def wm(redis_client):
    return WorkingMemory(redis_client, ttl_seconds=100, transcript_limit=30)
