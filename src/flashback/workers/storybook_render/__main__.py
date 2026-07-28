"""CLI entrypoint for the storybook_render worker.

    python -m flashback.workers.storybook_render run
    python -m flashback.workers.storybook_render run-once --storybook-id <uuid>

All config comes from environment variables
(flashback.config.StorybookRenderConfig).
"""
from __future__ import annotations

import argparse
import sys
import tempfile

from flashback.config import StorybookRenderConfig
from flashback.db.connection import make_pool
from flashback.http.logging import configure_logging

from . import persistence
from .sqs_client import SQSClient
from .worker import _openai_client, render_and_upload, run_forever


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m flashback.workers.storybook_render",
        description="Storybook PDF/page render worker.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Drain the storybook_render queue (long-running).")
    once = sub.add_parser(
        "run-once", help="Render one storybook by id (no queue)."
    )
    once.add_argument("--storybook-id", required=True)
    return parser


def _cmd_run(cfg: StorybookRenderConfig) -> int:
    pool = make_pool(cfg.database_url, min_size=cfg.db_pool_min_size,
                     max_size=cfg.db_pool_max_size)
    sqs = SQSClient(queue_url=cfg.storybook_render_queue_url,
                    region_name=cfg.aws_region)
    try:
        run_forever(pool=pool, cfg=cfg, sqs=sqs)
    finally:
        pool.close()
    return 0


def _cmd_run_once(cfg: StorybookRenderConfig, *, storybook_id: str) -> int:
    from google import genai

    pool = make_pool(cfg.database_url, min_size=cfg.db_pool_min_size,
                     max_size=cfg.db_pool_max_size)
    gemini_client = genai.Client(api_key=cfg.gemini_api_key)
    verifier = _openai_client(cfg)
    try:
        ctx = persistence.load_render_context(pool, storybook_id=storybook_id)
        if ctx is None:
            print(f"no render context for storybook {storybook_id}")
            return 1
        with tempfile.TemporaryDirectory() as td:
            pdf_ok, pages, cover_ok = render_and_upload(
                ctx, pool=pool, cfg=cfg, gemini_client=gemini_client,
                verifier=verifier, tmpdir=td)
        persistence.mark_complete(
            pool, storybook_id=ctx.storybook_id, person_id=ctx.person_id,
            collection=ctx.collection, pdf_present=pdf_ok,
            pages_present=pages, cover_present=cover_ok)
        print(f"rendered storybook {storybook_id} pdf={pdf_ok} "
              f"pages={pages} cover={cover_ok}")
    finally:
        pool.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return _cmd_run(StorybookRenderConfig.from_env())
    if args.command == "run-once":
        return _cmd_run_once(
            StorybookRenderConfig.from_env(queue_required=False),
            storybook_id=args.storybook_id)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
