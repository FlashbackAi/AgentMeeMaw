"""Optional HTTP idempotency helpers for mutating endpoints."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import Header, HTTPException, status
from pydantic import BaseModel
from redis.asyncio import Redis

T = TypeVar("T", bound=BaseModel)

IDEMPOTENCY_TTL_SECONDS = 86400
IN_FLIGHT_TTL_SECONDS = 120


def idempotency_key_header(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    if idempotency_key is None:
        return None
    value = idempotency_key.strip()
    if not value:
        return None
    if len(value) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="idempotency key too long",
        )
    return value


async def run_idempotent(
    redis: Redis,
    *,
    scope: str,
    key: str | None,
    response_model: type[T],
    operation: Callable[[], Awaitable[T]],
) -> T:
    if key is None:
        return await operation()

    cache_key = f"http:idempotency:{scope}:{key}"
    lock_key = f"{cache_key}:lock"

    cached = await redis.get(cache_key)
    if cached:
        return response_model.model_validate_json(cached)

    acquired = await redis.set(lock_key, "1", nx=True, ex=IN_FLIGHT_TTL_SECONDS)
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="request with this idempotency key is already in progress",
        )

    try:
        result = await operation()
        await redis.set(
            cache_key,
            json.dumps(result.model_dump(mode="json")),
            ex=IDEMPOTENCY_TTL_SECONDS,
        )
        return result
    finally:
        await redis.delete(lock_key)
