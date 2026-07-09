from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

from flashback.db.connection import make_pool
from flashback.usage.pricing import compute_cost, compute_image_cost
from flashback.usage.queries import INSERT_USAGE_EVENT

log = structlog.get_logger("flashback.usage")

_pool = None  # lazily created sync pool; not bound to any event loop


def reset_pool_for_tests() -> None:
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:  # noqa: BLE001
            pass
    _pool = None


def _get_pool():
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            return None
        _pool = make_pool(dsn, max_size=2)
    return _pool


def insert_event(row: dict[str, Any]) -> str | None:
    """Insert one usage_events row. Never raises — logs and returns None on failure."""
    pool = _get_pool()
    if pool is None:
        return None
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_USAGE_EVENT, row)
                return cur.fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        log.warning("usage.record_failed", feature=row.get("feature"), error=str(exc))
        return None


def record_llm_usage_sync(
    *,
    feature: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    person_id: str | None = None,
    session_id: str | None = None,
) -> None:
    cost = compute_cost(
        provider, model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
    )
    insert_event({
        "service": "agent",
        "feature": feature,
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "units": None,
        "unit_type": "tokens",
        "cost_usd": cost,
        "person_id": person_id,
        "session_id": session_id,
    })


def record_image_usage_sync(
    *,
    feature: str,
    provider: str,
    model: str,
    images: int = 1,
    person_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Meter one image-generation call (agent-side Gemini renders).

    Same soft-fail contract as the LLM recorder: metering must never
    break a render, so failures log and drop.
    """
    insert_event({
        "service": "agent",
        "feature": feature,
        "provider": provider,
        "model": model,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "units": images,
        "unit_type": "images",
        "cost_usd": compute_image_cost(provider, model, images=images),
        "person_id": person_id,
        "session_id": session_id,
    })


async def record_llm_usage(
    *,
    feature: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    person_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Async entry: runs the sync insert in a thread so it never blocks the loop
    and never binds a pool to a per-message worker loop."""
    await asyncio.to_thread(
        record_llm_usage_sync,
        feature=feature, provider=provider, model=model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
        person_id=person_id, session_id=session_id,
    )
