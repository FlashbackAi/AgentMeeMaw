"""CLI entrypoint for the tribute_render worker.

    python -m flashback.workers.tribute_render run
    python -m flashback.workers.tribute_render run-once --tribute-id <uuid>

All config comes from environment variables (flashback.config.TributeRenderConfig).
"""
from __future__ import annotations

import argparse
import sys
import tempfile

from flashback.config import TributeRenderConfig
from flashback.db.connection import make_pool
from flashback.http.logging import configure_logging

from . import persistence
from .sqs_client import SQSClient
from .worker import render_and_upload, run_forever


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m flashback.workers.tribute_render",
        description="Tribute video/PDF render worker.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Drain the tribute_render queue (long-running).")
    once = sub.add_parser("run-once", help="Render one tribute by id (no queue).")
    once.add_argument("--tribute-id", required=True)
    return parser


def _cmd_run(cfg: TributeRenderConfig) -> int:
    pool = make_pool(cfg.database_url, min_size=cfg.db_pool_min_size,
                     max_size=cfg.db_pool_max_size)
    sqs = SQSClient(queue_url=cfg.tribute_render_queue_url,
                    region_name=cfg.aws_region)
    try:
        run_forever(pool=pool, cfg=cfg, sqs=sqs)
    finally:
        pool.close()
    return 0


def _cmd_run_once(cfg: TributeRenderConfig, *, tribute_id: str) -> int:
    from flashback.tribute_video.art import Artist
    pool = make_pool(cfg.database_url, min_size=cfg.db_pool_min_size,
                     max_size=cfg.db_pool_max_size)
    artist = Artist(api_key=cfg.gemini_api_key, model=cfg.gemini_image_model)
    try:
        ctx = persistence.load_render_context(pool, tribute_id=tribute_id)
        if ctx is None:
            print(f"no render context for tribute {tribute_id}")
            return 1
        with tempfile.TemporaryDirectory() as td:
            video_ok, pdf_ok = render_and_upload(ctx, artist=artist, tmpdir=td)
        persistence.mark_complete(pool, tribute_id=ctx.tribute_id,
                                  person_id=ctx.person_id,
                                  video_present=video_ok, pdf_present=pdf_ok)
        print(f"rendered tribute {tribute_id} video={video_ok} pdf={pdf_ok}")
    finally:
        pool.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return _cmd_run(TributeRenderConfig.from_env())
    if args.command == "run-once":
        return _cmd_run_once(
            TributeRenderConfig.from_env(queue_required=False),
            tribute_id=args.tribute_id)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
