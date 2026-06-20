"""The tribute_render drain loop.

Each message: load the render context off the row (skip if missing/stale) ->
download the prime photo -> render MP4 + PDF -> upload via presigned PUTs ->
flip status + NOTIFY. Ack on ok/skip; on failure DON'T ack so SQS redrives,
landing in the DLQ after maxReceiveCount (Node falls back to a wrap timeout).
"""
from __future__ import annotations

import os
import signal
import tempfile

import structlog

from flashback.tribute_video import transfer
from flashback.tribute_video.art import Artist
from flashback.tribute_video.context import RenderContext
from flashback.tribute_video.render import render_book

from . import persistence
from .sqs_client import SQSClient, TributeRenderMessage

log = structlog.get_logger("flashback.workers.tribute_render")


def render_and_upload(ctx: RenderContext, *, artist: Artist,
                      tmpdir: str) -> tuple[bool, bool]:
    """Render the artifacts and PUT them to the presigned URLs."""
    photo = (transfer.download_image(ctx.prime_photo_get_url)
             if ctx.prime_photo_get_url else None)
    pdf_path = os.path.join(tmpdir, f"{ctx.tribute_id}.pdf")
    mp4_path = os.path.join(tmpdir, f"{ctx.tribute_id}.mp4")
    render_book(
        book=ctx.book, subject_name=ctx.subject_name,
        relationship=ctx.relationship, gt_context=ctx.gt_context,
        artist=artist, pdf_path=pdf_path, mp4_path=mp4_path,
        prime_photo=photo, deage=ctx.deage, blend=ctx.blend,
        transition=ctx.transition, fps=ctx.fps,
    )
    video_ok = 200 <= transfer.upload_file(
        ctx.video_put_url, mp4_path, content_type="video/mp4") < 300
    pdf_ok = 200 <= transfer.upload_file(
        ctx.pdf_put_url, pdf_path, content_type="application/pdf") < 300
    return video_ok, pdf_ok


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
    video_present, pdf_present = run_render(ctx)
    mark_complete(ctx.tribute_id, ctx.person_id, video_present, pdf_present)
    log.info("tribute_render.complete", tribute_id=ctx.tribute_id,
             video=video_present, pdf=pdf_present)
    return "ok"


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

    def _render(ctx: RenderContext) -> tuple[bool, bool]:
        with tempfile.TemporaryDirectory() as td:
            return render_and_upload(ctx, artist=artist, tmpdir=td)

    def _complete(tid: str, pid: str, video: bool, pdf: bool) -> None:
        persistence.mark_complete(pool, tribute_id=tid, person_id=pid,
                                  video_present=video, pdf_present=pdf)

    log.info("tribute_render.worker_started",
             max_messages=cfg.sqs_max_messages, wait_seconds=cfg.sqs_wait_seconds)
    while not stop.requested:
        for msg in sqs.receive(max_messages=cfg.sqs_max_messages,
                               wait_seconds=cfg.sqs_wait_seconds):
            try:
                process_one(msg, load_context=_load, run_render=_render,
                            mark_complete=_complete)
                sqs.delete(msg.receipt_handle)
            except Exception as exc:  # transient -> redrive -> DLQ
                log.error("tribute_render.failed", tribute_id=msg.tribute_id,
                          error_type=type(exc).__name__, error=str(exc)[:200])
    log.info("tribute_render.worker_stopped")
