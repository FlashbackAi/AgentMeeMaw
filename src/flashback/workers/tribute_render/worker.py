"""The tribute_render drain loop.

Each message: load the render context off the row (skip if missing/stale) ->
assemble the Book (big-LLM) -> download the prime photo -> render MP4 + PDF ->
upload via presigned PUTs -> flip status + NOTIFY. Ack on ok/skip; on failure
DON'T ack so SQS redrives, marking the row 'failed' on the final attempt.

Book assembly moved here from POST /tributes/{id}/generate: the LLM call takes
~30s, which tripped Node's request timeout when it ran inline. The render
context now carries the assembly inputs; the heavy work is fully async.
"""
from __future__ import annotations

import asyncio
import os
import signal
import tempfile

import structlog

from flashback.tribute_video import transfer
from flashback.tribute_video.art import Artist
from flashback.tribute_video.assembler import assemble_storybook_video
from flashback.tribute_video.book import Book
from flashback.tribute_video.context import RenderContext
from flashback.tribute_video.remotion_render import (
    recipe_kwargs_from_style,
    render_book_remotion,
)
from flashback.tribute_video.render import render_book
from flashback.tribute_video.style import StyleKit, kit_from_style_dict

from . import persistence
from .sqs_client import SQSClient, TributeRenderMessage

log = structlog.get_logger("flashback.workers.tribute_render")


def assemble_book(ctx: RenderContext, *, settings) -> Book:
    """Build the storybook Book from the context inputs (big-LLM, sync wrapper).

    assemble_storybook_video is async; the worker is sync, so run it in a
    throwaway event loop. It falls back to title-derived beats internally if
    the LLM call fails, so this never raises for a degraded model output.
    """
    return asyncio.run(assemble_storybook_video(
        settings=settings,
        subject_name=ctx.subject_name,
        relationship=ctx.relationship,
        gt_context=ctx.gt_context,
        candidates=ctx.candidates,
        message_text=ctx.message_text,
        archetype_leads=ctx.archetype_leads,
        edit_instructions=ctx.edit_instructions,
        n_pages=ctx.n_pages,
        voice_block=ctx.voice_block or None,
        opener_style=ctx.opener_style or None,
        art_mood=ctx.art_mood or None,
        narrative_block=ctx.narrative_block or None,
        fallback_opener=ctx.fallback_opener,
        fallback_closing=ctx.fallback_closing,
    ))


def build_style_kit(ctx: RenderContext, *, pool, tmpdir: str) -> StyleKit:
    """Resolve the snapshot's style dict into a StyleKit.

    When the snapshot pins a visual theme whose row carries template bytes,
    write them to a tmp file for Pillow; anything missing falls back to the
    built-in kit fields (a render never blocks on config).
    """
    template_path: str | None = None
    theme_id = (ctx.style or {}).get("visual_theme_id")
    if theme_id and pool is not None:
        try:
            found = persistence.load_visual_theme_image_sync(pool, theme_id=theme_id)
        except Exception:
            log.warning("tribute_render.template_load_failed",
                        tribute_id=ctx.tribute_id, theme_id=str(theme_id),
                        exc_info=True)
            found = None
        if found is not None:
            image_bytes, _mime = found
            template_path = os.path.join(tmpdir, "page-template-override.img")
            with open(template_path, "wb") as fh:
                fh.write(image_bytes)
    return kit_from_style_dict(ctx.style, template_override_path=template_path)


def render_and_upload(ctx: RenderContext, *, artist: Artist,
                      tmpdir: str, settings,
                      kit: StyleKit | None = None) -> tuple[bool, bool, bool]:
    """Assemble the Book, render the artifacts, and PUT them to the URLs.

    Returns (video_ok, pdf_ok, poster_ok). The poster is the opener page (the
    cover: portrait + title); it's only rendered + uploaded when Node minted a
    poster_put_url, so the card/thumbnail can show the cover rather than a
    stray video frame.
    """
    book = assemble_book(ctx, settings=settings)
    log.info("tribute_render.assembled", tribute_id=ctx.tribute_id,
             beats=len(book.beats))
    photo = (transfer.download_image(ctx.prime_photo_get_url)
             if ctx.prime_photo_get_url else None)
    pdf_path = os.path.join(tmpdir, f"{ctx.tribute_id}.pdf")
    mp4_path = os.path.join(tmpdir, f"{ctx.tribute_id}.mp4")
    poster_path = (os.path.join(tmpdir, f"{ctx.tribute_id}.poster.jpg")
                   if ctx.poster_put_url else None)
    # Engine selection: "remotion" (Node subprocess, the default) with an
    # automatic fallback to the legacy Pillow+ffmpeg render, so a Remotion
    # failure never strands a tribute in 'failed'. ``transition`` is
    # ffmpeg-only, added to the legacy call alone (spec 2026-07-20 §11).
    # A visual theme may pin its engine via the snapshot (0045) — occasions
    # that must keep the legacy look (Father's Day) override the default.
    style_recipe = ((ctx.style or {}).get("recipe") or {}) if isinstance(ctx.style, dict) else {}
    pinned = str(style_recipe.get("render_engine") or "").strip()
    engine = pinned or getattr(settings, "render_engine", "remotion")
    render_kwargs = dict(
        book=book, subject_name=ctx.subject_name,
        relationship=ctx.relationship, gt_context=ctx.gt_context,
        artist=artist, pdf_path=pdf_path, mp4_path=mp4_path,
        poster_path=poster_path,
        prime_photo=photo, deage=ctx.deage, blend=ctx.blend, fps=ctx.fps,
        concurrency=getattr(settings, "render_concurrency", 4),
        kit=kit, art_mood=ctx.art_mood or None,
    )
    if engine == "remotion":
        try:
            recipe_kwargs = recipe_kwargs_from_style(ctx.style)
            render_book_remotion(**render_kwargs, **recipe_kwargs)
        except Exception as exc:  # noqa: BLE001 - fall back, never fail the render
            log.warning("tribute_render.remotion_failed_fallback_legacy",
                        tribute_id=ctx.tribute_id, error=str(exc)[:300])
            render_book(transition=ctx.transition, **render_kwargs)
    else:
        render_book(transition=ctx.transition, **render_kwargs)
    video_ok = 200 <= transfer.upload_file(
        ctx.video_put_url, mp4_path, content_type="video/mp4") < 300
    pdf_ok = 200 <= transfer.upload_file(
        ctx.pdf_put_url, pdf_path, content_type="application/pdf") < 300
    poster_ok = False
    if poster_path is not None:
        poster_ok = 200 <= transfer.upload_file(
            ctx.poster_put_url, poster_path, content_type="image/jpeg") < 300
    return video_ok, pdf_ok, poster_ok


def process_one(msg: TributeRenderMessage, *, load_context, run_render,
                mark_complete) -> str:
    """Orchestrate one render. Callables are injected so this is unit-testable.

    Returns "ok" | "skip" (both ack). Raises on failure (caller must not ack).
    """
    ctx = load_context(msg.tribute_id, msg.composed_at)
    if ctx is None:
        log.info("tribute_render.skip", tribute_id=msg.tribute_id,
                 reason="missing_or_stale")
        return "skip"
    video_present, pdf_present, poster_present = run_render(ctx)
    mark_complete(ctx.tribute_id, ctx.person_id, video_present, pdf_present,
                  poster_present)
    log.info("tribute_render.complete", tribute_id=ctx.tribute_id,
             video=video_present, pdf=pdf_present, poster=poster_present)
    return "ok"


def handle_failure(msg: TributeRenderMessage, exc: Exception, *,
                   max_attempts: int, mark_failed, ack) -> str:
    """Decide what to do after a render exception.

    Returns "retry" when the message should be left unacked for SQS to
    redrive, or "failed" when retries are exhausted -- in which case the
    row is marked terminally failed and the message acked (so it does not
    pointlessly cycle to the DLQ, where nothing would advance the status).
    """
    if msg.receive_count >= max_attempts:
        mark_failed(msg.tribute_id, f"{type(exc).__name__}: {exc}")
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


def run_forever(*, pool, cfg, sqs: SQSClient, stop: _StopSignal | None = None) -> None:
    stop = stop or _StopSignal()
    stop.install()
    artist = Artist(api_key=cfg.gemini_api_key, model=cfg.gemini_image_model)

    def _load(tid: str, composed_at: str):
        return persistence.load_render_context(
            pool, tribute_id=tid, composed_at=composed_at)

    def _render(ctx: RenderContext) -> tuple[bool, bool, bool]:
        with tempfile.TemporaryDirectory() as td:
            kit = build_style_kit(ctx, pool=pool, tmpdir=td)
            return render_and_upload(ctx, artist=artist, tmpdir=td,
                                     settings=cfg, kit=kit)

    def _complete(tid: str, pid: str, video: bool, pdf: bool,
                  poster: bool) -> None:
        persistence.mark_complete(pool, tribute_id=tid, person_id=pid,
                                  video_present=video, pdf_present=pdf,
                                  poster_present=poster)

    def _fail(tid: str, error: str) -> None:
        persistence.mark_failed(pool, tribute_id=tid, error=error)

    log.info("tribute_render.worker_started",
             max_messages=cfg.sqs_max_messages, wait_seconds=cfg.sqs_wait_seconds)
    while not stop.requested:
        for msg in sqs.receive(max_messages=cfg.sqs_max_messages,
                               wait_seconds=cfg.sqs_wait_seconds):
            try:
                process_one(msg, load_context=_load, run_render=_render,
                            mark_complete=_complete)
                sqs.delete(msg.receipt_handle)
            except Exception as exc:  # transient -> redrive; terminal -> failed
                log.error("tribute_render.failed", tribute_id=msg.tribute_id,
                          attempt=msg.receive_count,
                          error_type=type(exc).__name__, error=str(exc)[:200])
                try:
                    outcome = handle_failure(
                        msg, exc, max_attempts=cfg.max_render_attempts,
                        mark_failed=_fail, ack=sqs.delete)
                    if outcome == "failed":
                        log.error("tribute_render.exhausted",
                                  tribute_id=msg.tribute_id,
                                  attempts=msg.receive_count)
                except Exception:  # mark/ack failed -> leave for redrive
                    log.exception("tribute_render.mark_failed_error",
                                  tribute_id=msg.tribute_id)
    log.info("tribute_render.worker_stopped")
