"""The storybook_render drain loop.

Each message: load the render context off the row (skip if missing/stale) ->
curate the pool + assemble the BookScript (big-LLM; skipped when the context
says reuse the stored script) -> build the age-anchored master identity refs
(from the anchor photo when Node minted a GET URL) -> render cover + pages +
PDF -> upload via presigned PUTs -> flip status + NOTIFY. Ack on ok/skip; on
failure DON'T ack so SQS redrives, marking the row 'failed' on the final
attempt.

All heavy LLM work happens here, never in the HTTP request (tribute pattern).
"""
from __future__ import annotations

import asyncio
import signal
import tempfile

import structlog
from google import genai

from flashback.storybook.collections import COLLECTIONS
from flashback.usage.context import bind_usage_context
from flashback.storybook.context import StorybookRenderContext
from flashback.storybook.refs import MasterRefs
from flashback.storybook.render import render_storybook
from flashback.storybook.script import BookScript, assemble_script
from flashback.tribute_video import transfer

from . import persistence
from .sqs_client import SQSClient, StorybookRenderMessage

log = structlog.get_logger("flashback.workers.storybook_render")


def _openai_client(cfg):
    import openai

    return openai.OpenAI(api_key=cfg.openai_api_key)


async def _assemble(ctx: StorybookRenderContext, *, settings) -> BookScript:
    """Assemble the book from the context's moment slice.

    ``ctx.moments`` IS the definitive slice — the route resolves it from the
    collection's tagged pool (or the user's confirmed pick) and writes it to
    Postgres before enqueuing (design 2026-07-06). The worker no longer curates
    or falls back to an unrelated pool, so it can never render a collection from
    moments that don't fit it."""
    return await assemble_script(
        settings=settings,
        collection=COLLECTIONS[ctx.collection],
        subject_name=ctx.subject_name,
        relationship=ctx.relationship,
        gt_context=ctx.gt_context,
        moments=ctx.moments,
        edit_instructions=ctx.edit_instructions or None,
        subject_gender=ctx.gender,
        contributor_gender=ctx.contributor_gender,
        people=ctx.people,
    )


def build_script(ctx: StorybookRenderContext, *, pool, settings) -> BookScript:
    """Reuse the stored script on regenerate; otherwise assemble (big-LLM,
    sync wrapper) from the context slice and persist the result on the row."""
    if ctx.reuse_script:
        saved = persistence.load_saved_script(pool, storybook_id=ctx.storybook_id)
        if saved:
            log.info("storybook_render.reusing_script",
                     storybook_id=ctx.storybook_id)
            return BookScript.from_dict(saved)
        log.info("storybook_render.reuse_missing_script",
                 storybook_id=ctx.storybook_id)
    script = asyncio.run(_assemble(ctx, settings=settings))
    persistence.save_script(
        pool, storybook_id=ctx.storybook_id,
        title=script.cover_title, script_dict=script.to_dict())
    return script


def render_and_upload(ctx: StorybookRenderContext, *, pool, cfg,
                      gemini_client, verifier,
                      tmpdir: str) -> tuple[bool, int, bool]:
    """Assemble (or reuse), render, and PUT the artifacts to the URLs.

    Returns (pdf_ok, pages_uploaded, cover_ok).
    """
    script = build_script(ctx, pool=pool, settings=cfg)
    anchor = (transfer.download_image(ctx.anchor_photo_get_url)
              if ctx.anchor_photo_get_url else None)
    refs = MasterRefs()
    refs.build(
        gemini_client,
        name=ctx.subject_name,
        gt_context=ctx.gt_context,
        gender=ctx.gender,
        anchor_photo=anchor,
        model=cfg.gemini_image_model,
    )
    result = render_storybook(
        script=script,
        collection=COLLECTIONS[ctx.collection],
        subject_name=ctx.subject_name,
        relationship=ctx.relationship,
        gt_context=ctx.gt_context,
        master_refs=refs,
        gemini_client=gemini_client,
        verifier=verifier,
        out_dir=tmpdir,
        model=cfg.gemini_image_model,
        gender=ctx.gender,
        concurrency=getattr(cfg, "render_concurrency", 4),
    )
    pdf_ok = 200 <= transfer.upload_file(
        ctx.pdf_put_url, result.pdf_path,
        content_type="application/pdf") < 300
    cover_ok = 200 <= transfer.upload_file(
        ctx.cover_put_url, result.cover_path,
        content_type="image/png") < 300
    pages_uploaded = 0
    for path, url in zip(result.page_paths, ctx.page_put_urls):
        if 200 <= transfer.upload_file(
                url, path, content_type="image/png") < 300:
            pages_uploaded += 1
    return pdf_ok, pages_uploaded, cover_ok


def process_one(msg: StorybookRenderMessage, *, load_context, run_render,
                mark_complete) -> str:
    """Orchestrate one render. Callables are injected so this is unit-testable.

    Returns "ok" | "skip" (both ack). Raises on failure (caller must not ack).
    """
    ctx = load_context(msg.storybook_id, msg.composed_at)
    if ctx is None:
        log.info("storybook_render.skip", storybook_id=msg.storybook_id,
                 reason="missing_or_stale")
        return "skip"
    with bind_usage_context(person_id=str(ctx.person_id)):
        pdf_present, pages_present, cover_present = run_render(ctx)
    mark_complete(ctx.storybook_id, ctx.person_id, ctx.collection,
                  pdf_present, pages_present, cover_present)
    log.info("storybook_render.complete", storybook_id=ctx.storybook_id,
             collection=ctx.collection, pdf=pdf_present,
             pages=pages_present, cover=cover_present)
    return "ok"


def handle_failure(msg: StorybookRenderMessage, exc: Exception, *,
                   max_attempts: int, mark_failed, ack) -> str:
    """Decide what to do after a render exception.

    Returns "retry" when the message should be left unacked for SQS to
    redrive, or "failed" when retries are exhausted -- in which case the row
    is marked terminally failed and the message acked (so it does not
    pointlessly cycle to the DLQ, where nothing would advance the status).
    """
    if msg.receive_count >= max_attempts:
        mark_failed(msg.storybook_id, f"{type(exc).__name__}: {exc}")
        ack(msg.receipt_handle)
        return "failed"
    return "retry"


class _StopSignal:
    def __init__(self) -> None:
        self.requested = False

    def install(self) -> None:
        signal.signal(signal.SIGINT, self._handle)
        try:
            signal.signal(signal.SIGTERM, self._handle)
        except (AttributeError, ValueError):
            pass

    def _handle(self, *_args) -> None:
        self.requested = True


def run_forever(*, pool, cfg, sqs: SQSClient,
                stop: _StopSignal | None = None) -> None:
    stop = stop or _StopSignal()
    stop.install()
    gemini_client = genai.Client(api_key=cfg.gemini_api_key)
    verifier = _openai_client(cfg)

    def _load(sid: str, composed_at: str):
        return persistence.load_render_context(
            pool, storybook_id=sid, composed_at=composed_at)

    def _render(ctx: StorybookRenderContext) -> tuple[bool, int, bool]:
        with tempfile.TemporaryDirectory() as td:
            return render_and_upload(ctx, pool=pool, cfg=cfg,
                                     gemini_client=gemini_client,
                                     verifier=verifier, tmpdir=td)

    def _complete(sid: str, pid: str, collection: str, pdf: bool,
                  pages: int, cover: bool) -> None:
        persistence.mark_complete(pool, storybook_id=sid, person_id=pid,
                                  collection=collection, pdf_present=pdf,
                                  pages_present=pages, cover_present=cover)

    def _fail(sid: str, error: str) -> None:
        persistence.mark_failed(pool, storybook_id=sid, error=error)

    log.info("storybook_render.worker_started",
             max_messages=cfg.sqs_max_messages,
             wait_seconds=cfg.sqs_wait_seconds)
    while not stop.requested:
        for msg in sqs.receive(max_messages=cfg.sqs_max_messages,
                               wait_seconds=cfg.sqs_wait_seconds):
            try:
                process_one(msg, load_context=_load, run_render=_render,
                            mark_complete=_complete)
                sqs.delete(msg.receipt_handle)
            except Exception as exc:  # transient -> redrive; terminal -> failed
                log.error("storybook_render.failed",
                          storybook_id=msg.storybook_id,
                          attempt=msg.receive_count,
                          error_type=type(exc).__name__,
                          error=str(exc)[:200])
                try:
                    outcome = handle_failure(
                        msg, exc, max_attempts=cfg.max_render_attempts,
                        mark_failed=_fail, ack=sqs.delete)
                    if outcome == "failed":
                        log.error("storybook_render.exhausted",
                                  storybook_id=msg.storybook_id,
                                  attempts=msg.receive_count)
                except Exception:  # mark/ack failed -> leave for redrive
                    log.exception("storybook_render.mark_failed_error",
                                  storybook_id=msg.storybook_id)
    log.info("storybook_render.worker_stopped")
