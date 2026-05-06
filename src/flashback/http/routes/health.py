"""Health check — Valkey + Postgres reachability."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from flashback.config import HttpConfig
from flashback.http.deps import get_db_pool, get_http_config, get_redis, get_sqs_client
from flashback.http.models import HealthResponse
from flashback.queues import AsyncSQSClient

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(
    response: Response,
    cfg: HttpConfig = Depends(get_http_config),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    redis: Redis = Depends(get_redis),
    sqs_client: AsyncSQSClient = Depends(get_sqs_client),
) -> HealthResponse:
    checks: dict[str, str] = {}
    ok = True

    try:
        await redis.ping()
        checks["valkey"] = "ok"
    except Exception as exc:  # noqa: BLE001 — health check must not propagate
        checks["valkey"] = f"error: {exc.__class__.__name__}"
        ok = False

    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {exc.__class__.__name__}"
        ok = False

    queue_checks = {
        "sqs.extraction": cfg.extraction_queue_url,
        "sqs.trait_synthesizer": cfg.trait_synthesizer_queue_url,
        "sqs.profile_summary": cfg.profile_summary_queue_url,
        "sqs.producers_per_session": cfg.producers_per_session_queue_url,
    }
    for name, queue_url in queue_checks.items():
        try:
            await sqs_client.get_queue_attributes(queue_url)
            checks[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks[name] = f"error: {exc.__class__.__name__}"
            ok = False

    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", checks=checks)
    return HealthResponse(status="ok", checks=checks)
