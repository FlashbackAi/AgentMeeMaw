"""
FastAPI application factory.

Run via uvicorn::

    uvicorn flashback.http.app:create_app --factory --host 0.0.0.0 --port 8000

The factory pattern lets tests construct an app with overridden
dependencies (in-process fakeredis, a Postgres test pool, a stub
orchestrator, etc.) without re-importing the module.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, cast

import redis.asyncio as redis_asyncio
import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from flashback.config import APP_VERSION, HttpConfig
from flashback.db.connection import make_async_pool
from flashback.http.errors import install_exception_handlers
from flashback.http.logging import (
    configure_logging,
    install_request_logging_middleware,
)
from flashback.http.routes.admin import router as admin_router
from flashback.http.routes.health import router as health_router
from flashback.http.routes.identity_merges import router as identity_merges_router
from flashback.http.routes.nodes import router as nodes_router
from flashback.http.routes.profile_facts import router as profile_facts_router
from flashback.http.routes.session import router as session_router
from flashback.http.routes.turn import router as turn_router
from flashback.identity_merges import IdentityMergeVerifier
from flashback.intent_classifier import IntentClassifier
from flashback.llm.interface import Provider
from flashback.orchestrator import Orchestrator, OrchestratorDeps
from flashback.phase_gate import PhaseGate, StarterSelector, SteadySelector
from flashback.queues import (
    AsyncSQSClient,
    ExtractionQueueProducer,
    ProducersPerSessionQueueProducer,
    ProfileSummaryQueueProducer,
    TraitSynthesizerQueueProducer,
)
from flashback.queues.boto import make_sqs_client
from flashback.response_generator import ResponseGenerator
from flashback.retrieval import RetrievalService, VoyageQueryEmbedder
from flashback.segment_detector import SegmentDetector
from flashback.session_summary import SessionSummaryGenerator
from flashback.working_memory import WorkingMemory


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boot and tear down the long-lived singletons.

    Order matters on startup: pool open before the WM client (so a
    health check during boot doesn't race), redis client before the
    orchestrator. Order is reversed on teardown.
    """
    cfg: HttpConfig = app.state.http_config

    db_pool = make_async_pool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
    )
    log = structlog.get_logger("flashback.http")
    redis_client = None
    closed_on_error = False
    try:
        await db_pool.open()
        app.state.db_pool = db_pool

        redis_client = redis_asyncio.from_url(cfg.valkey_url)
        app.state.redis = redis_client

        wm = WorkingMemory(
            redis_client=redis_client,
            ttl_seconds=cfg.working_memory_ttl_seconds,
            transcript_limit=cfg.working_memory_transcript_limit,
        )
        app.state.working_memory = wm
        retrieval = RetrievalService(
            db_pool=db_pool,
            voyage_embedder=VoyageQueryEmbedder.from_api_key(
                cfg.voyage_api_key,
                model=cfg.embedding_model,
                timeout=cfg.retrieval_query_embed_timeout_seconds,
            ),
            embedding_model=cfg.embedding_model,
            embedding_model_version=cfg.embedding_model_version,
            default_limit=cfg.retrieval_default_limit,
            max_limit=cfg.retrieval_max_limit,
        )
        app.state.retrieval = retrieval
        intent_classifier = IntentClassifier(
            settings=cfg,
            provider=cast(Provider, cfg.llm_small_provider),
            model=cfg.llm_intent_model,
            timeout=cfg.llm_intent_timeout_seconds,
            max_tokens=cfg.llm_intent_max_tokens,
        )
        response_generator = ResponseGenerator(
            settings=cfg,
            provider=cast(Provider, cfg.llm_response_provider),
            model=cfg.llm_response_model,
            timeout=cfg.llm_response_timeout_seconds,
            max_tokens=cfg.llm_response_max_tokens,
        )
        segment_detector = SegmentDetector(
            settings=cfg,
            provider=cast(Provider, cfg.llm_segment_detector_provider),
            model=cfg.llm_segment_detector_model,
            timeout=cfg.llm_segment_detector_timeout_seconds,
            max_tokens=cfg.llm_segment_detector_max_tokens,
        )
        app.state.identity_merge_verifier = IdentityMergeVerifier(
            settings=cfg,
            provider=cast(Provider, cfg.llm_small_provider),
            model=cfg.llm_small_model,
            timeout=cfg.llm_intent_timeout_seconds,
            max_tokens=cfg.llm_intent_max_tokens,
        )
        sqs_client = AsyncSQSClient(
            make_sqs_client(cfg.aws_region),
        )
        app.state.sqs_client = sqs_client
        extraction_queue = ExtractionQueueProducer(
            sqs_client=sqs_client,
            queue_url=cfg.extraction_queue_url,
        )
        trait_synthesizer_queue = TraitSynthesizerQueueProducer(
            sqs_client=sqs_client,
            queue_url=cfg.trait_synthesizer_queue_url,
        )
        profile_summary_queue = ProfileSummaryQueueProducer(
            sqs_client=sqs_client,
            queue_url=cfg.profile_summary_queue_url,
        )
        producers_per_session_queue = ProducersPerSessionQueueProducer(
            sqs_client=sqs_client,
            queue_url=cfg.producers_per_session_queue_url,
        )
        session_summary_generator = SessionSummaryGenerator(settings=cfg)
        phase_gate = PhaseGate(
            db_pool=db_pool,
            starter_selector=StarterSelector(db_pool),
            steady_selector=SteadySelector(db_pool, wm),
        )
        orchestrator_deps = OrchestratorDeps(
            db_pool=db_pool,
            working_memory=wm,
            intent_classifier=intent_classifier,
            retrieval=retrieval,
            phase_gate=phase_gate,
            response_generator=response_generator,
            segment_detector=segment_detector,
            extraction_queue=extraction_queue,
            session_summary_generator=session_summary_generator,
            trait_synthesizer_queue=trait_synthesizer_queue,
            profile_summary_queue=profile_summary_queue,
            producers_per_session_queue=producers_per_session_queue,
            settings=cfg,
        )
        app.state.orchestrator_deps = orchestrator_deps
        app.state.orchestrator = Orchestrator(orchestrator_deps)

        if cfg.service_token_auth_disabled:
            log.warning("service_token_auth_disabled")
        log.info("service.started")

        yield
    except Exception:
        if redis_client is not None:
            await redis_client.aclose()
        await db_pool.close()
        closed_on_error = True
        raise
    finally:
        log.info("service.stopping")
        if not closed_on_error:
            if redis_client is not None:
                await redis_client.aclose()
            await db_pool.close()


def create_app(http_config: HttpConfig | None = None) -> FastAPI:
    """Build the FastAPI app.

    Tests pass their own ``http_config`` plus override
    ``app.dependency_overrides`` for the singletons. Production lets
    this read environment variables via ``HttpConfig.from_env()``.
    """
    configure_logging()
    cfg = http_config or HttpConfig.from_env()

    app = FastAPI(
        title="Flashback Agent",
        version=APP_VERSION,
        lifespan=_lifespan,
        docs_url=None,  # internal service; no public OpenAPI surface
        redoc_url=None,
    )
    app.state.http_config = cfg

    install_exception_handlers(app)
    install_request_size_middleware(app, max_bytes=cfg.max_request_body_bytes)
    if cfg.trusted_hosts != ("*",):
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(cfg.trusted_hosts))
    install_request_logging_middleware(app)

    app.include_router(health_router)
    app.include_router(session_router)
    app.include_router(turn_router)
    app.include_router(admin_router)
    app.include_router(profile_facts_router)
    app.include_router(identity_merges_router)
    app.include_router(nodes_router)

    return app


def install_request_size_middleware(app: FastAPI, *, max_bytes: int) -> None:
    @app.middleware("http")
    async def _limit_request_size(request: Request, call_next):
        content_length = request.headers.get("content-length")
        try:
            request_size = int(content_length) if content_length is not None else 0
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "invalid content-length"},
            )
        if request_size > max_bytes:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "request body too large"},
            )
        return await call_next(request)
