"""``POST /nodes/{node_type}/{id}/edit`` — generic node-edit endpoint.

Body shape: :class:`flashback.node_edits.NodeEditRequest`.

Behaviour:

* Resolves the type via :data:`flashback.node_edits.REGISTRY` (v1:
  ``moment``, ``entity``).
* Loads the active row scoped to ``person_id`` (refuses cross-legacy
  edits).
* Runs the per-type edit-LLM (Claude Sonnet by default) to re-derive
  the structured fields from the contributor's free-text edit.
* For moments: supersedes the old row, inserts the new row + entities,
  repoints inbound edges, drops outbound edges, and emits fresh
  ``involves`` / ``happened_at`` edges. Runs the identity-merge
  suggestion scan over the new entities.
* For entities: UPDATEs the row in place, clears embedding columns.
* Pushes embedding job(s) for any embedded fields, and an
  ``artifact_generation`` job per the registry's ``artifact_regen``
  flag.

Auth: ``require_service_token``, same as every other write route. Node
is the auth boundary.

Idempotency: optional ``Idempotency-Key`` header, scoped per
``(node_type, id)``. Same Redis-backed pattern as
``/identity_merges/.../approve``.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError
from redis.asyncio import Redis

from flashback.config import HttpConfig
from flashback.http.auth import require_service_token
from flashback.http.deps import get_db_pool, get_http_config, get_redis
from flashback.http.idempotency import idempotency_key_header, run_idempotent
from flashback.llm.errors import LLMError, LLMMalformedResponse, LLMTimeout
from flashback.node_edits import (
    NodeEditRequest,
    NodeEditResponse,
    REGISTRY,
    edit_node,
)
from flashback.node_edits.engine import (
    EditLLMOutputInvalid,
    NodeNotFound,
    PersonNotFound,
    UnknownNodeType,
)
from flashback.node_edits.strategies import EntityEditLostUpdate
from flashback.workers.extraction.sqs_client import (
    ArtifactJobSender,
    EmbeddingJobSender,
)

router = APIRouter(
    prefix="/nodes",
    dependencies=[Depends(require_service_token)],
)
log = structlog.get_logger("flashback.http.nodes")


@router.post(
    "/{node_type}/{node_id}/edit",
    response_model=NodeEditResponse,
)
async def edit(
    node_type: Literal["moment", "entity"],
    node_id: UUID,
    body: NodeEditRequest,
    idempotency_key: str | None = Depends(idempotency_key_header),
    redis: Redis = Depends(get_redis),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    cfg: HttpConfig = Depends(get_http_config),
) -> NodeEditResponse:
    """Run a generic edit on one node."""
    structlog.contextvars.bind_contextvars(
        node_type=node_type,
        node_id=str(node_id),
        person_id=str(body.person_id),
    )

    config = REGISTRY.get(node_type)
    if config is None:
        # Pydantic's Literal already enforces this for the path param;
        # this guard is for forward-compat when registry entries are
        # added without the route's literal being widened.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown node_type {node_type!r}",
        )

    if not cfg.embedding_queue_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="EMBEDDING_QUEUE_URL not configured",
        )
    if config.artifact_regen and not cfg.artifact_queue_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ARTIFACT_QUEUE_URL not configured",
        )

    return await run_idempotent(
        redis,
        scope=f"node_edit:{node_type}:{node_id}",
        key=idempotency_key,
        response_model=NodeEditResponse,
        operation=lambda: _edit_once(
            node_type=node_type,
            node_id=str(node_id),
            person_id=str(body.person_id),
            free_text=body.free_text,
            db_pool=db_pool,
            cfg=cfg,
        ),
    )


async def _edit_once(
    *,
    node_type: str,
    node_id: str,
    person_id: str,
    free_text: str,
    db_pool: AsyncConnectionPool,
    cfg: HttpConfig,
) -> NodeEditResponse:
    embedding_sender = EmbeddingJobSender(
        queue_url=cfg.embedding_queue_url,
        region_name=cfg.aws_region,
    )
    artifact_sender: ArtifactJobSender | None = None
    if cfg.artifact_queue_url:
        artifact_sender = ArtifactJobSender(
            queue_url=cfg.artifact_queue_url,
            region_name=cfg.aws_region,
        )

    try:
        result = await edit_node(
            node_type=node_type,
            node_id=node_id,
            person_id=person_id,
            free_text=free_text,
            db_pool=db_pool,
            cfg=cfg,
            push_embedding=embedding_sender.send,
            push_artifact=artifact_sender.send if artifact_sender else None,
        )
    except UnknownNodeType as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except (NodeNotFound, PersonNotFound) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except EntityEditLostUpdate as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "entity was changed concurrently; refresh and try again"
            ),
        ) from exc
    except (EditLLMOutputInvalid, ValidationError) as exc:
        log.error("node_edits.llm_output_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="edit-LLM output failed validation",
        ) from exc
    except LLMTimeout as exc:
        log.error("node_edits.llm_timeout", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="edit-LLM timed out",
        ) from exc
    except (LLMError, LLMMalformedResponse) as exc:
        log.error("node_edits.llm_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="edit-LLM call failed",
        ) from exc

    return NodeEditResponse(
        node_type=result.node_type,  # type: ignore[arg-type]
        node_id=UUID(result.node_id),
        superseded_id=UUID(result.superseded_id) if result.superseded_id else None,
        new_entity_ids=[UUID(eid) for eid in result.new_entity_ids],
        edges_added=result.edges_added,
        edges_removed=result.edges_removed,
        artifact_queued=result.artifact_queued,
        embedding_jobs_pushed=result.embedding_jobs_pushed,
    )
