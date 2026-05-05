"""Identity merge review endpoints."""

from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.config import HttpConfig
from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool, get_http_config
from flashback.identity_merges import (
    IdentityMergeActionResponse,
    IdentityMergeScanRequest,
    IdentityMergeScanResponse,
    IdentityMergeSuggestion,
    IdentityMergeVerifier,
    approve_merge_async,
    list_suggestions_async,
    reject_merge_async,
    scan_identity_merge_suggestions_async,
)
from flashback.llm.interface import Provider
from flashback.workers.extraction.sqs_client import EmbeddingJobSender

router = APIRouter(
    prefix="/identity_merges",
    dependencies=[Depends(require_service_token)],
)
log = structlog.get_logger("flashback.http.identity_merges")


@router.get("/suggestions", response_model=list[IdentityMergeSuggestion])
async def list_suggestions(
    person_id: UUID,
    status_filter: Literal["pending", "approved", "rejected"] = "pending",
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> list[IdentityMergeSuggestion]:
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            return await list_suggestions_async(
                cur,
                person_id=str(person_id),
                status=status_filter,
            )


@router.post("/scan", response_model=IdentityMergeScanResponse)
async def scan_suggestions(
    request: IdentityMergeScanRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    cfg: HttpConfig = Depends(get_http_config),
) -> IdentityMergeScanResponse:
    verifier = IdentityMergeVerifier(
        settings=cfg,
        provider=cast(Provider, cfg.llm_small_provider),
        model=cfg.llm_small_model,
        timeout=cfg.llm_intent_timeout_seconds,
        max_tokens=cfg.llm_intent_max_tokens,
    )
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await scan_identity_merge_suggestions_async(
                    cur,
                    person_id=str(request.person_id),
                    verifier=verifier.verify,
                    limit=request.limit,
                )
    log.info(
        "identity_merge.scan_completed",
        person_id=str(request.person_id),
        candidates_considered=result.candidates_considered,
        suggestions_created=result.suggestions_created,
    )
    return result


@router.post(
    "/suggestions/{suggestion_id}/approve",
    response_model=IdentityMergeActionResponse,
)
async def approve_suggestion(
    suggestion_id: UUID,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    cfg: HttpConfig = Depends(get_http_config),
) -> IdentityMergeActionResponse:
    if not cfg.embedding_queue_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="EMBEDDING_QUEUE_URL not configured",
        )

    sender = EmbeddingJobSender(
        queue_url=cfg.embedding_queue_url,
        region_name=cfg.aws_region,
    )
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await approve_merge_async(
                    cur,
                    suggestion_id=str(suggestion_id),
                    push_embedding=sender.send,
                    embedding_model=cfg.embedding_model,
                    embedding_model_version=cfg.embedding_model_version,
                )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="pending merge suggestion not found",
        )

    log.info(
        "identity_merge.approved",
        suggestion_id=str(suggestion_id),
        source_entity_id=str(result.source_entity_id),
        target_entity_id=str(result.target_entity_id),
    )
    return result


@router.post(
    "/suggestions/{suggestion_id}/reject",
    response_model=IdentityMergeActionResponse,
)
async def reject_suggestion(
    suggestion_id: UUID,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> IdentityMergeActionResponse:
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                result = await reject_merge_async(
                    cur,
                    suggestion_id=str(suggestion_id),
                )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="pending merge suggestion not found",
        )
    log.info("identity_merge.rejected", suggestion_id=str(suggestion_id))
    return result
