"""
CLI entrypoint for the embedding worker.

Two subcommands::

    python -m flashback.workers.embedding run
    python -m flashback.workers.embedding backfill [--record-type X] [--dry-run]

All configuration comes from environment variables (see
``flashback.config``); there are no config files. The model identity
the backfill stamps onto every enqueued job is read from
``EMBEDDING_MODEL`` and ``EMBEDDING_MODEL_VERSION``.
"""

from __future__ import annotations

import argparse
import sys

from flashback.config import Config
from flashback.db.connection import make_pool
from flashback.db.embedding_targets import EMBEDDING_TARGETS
from flashback.http.logging import configure_logging

from .backfill import backfill
from .sqs_client import SQSClient
from .voyage_client import VoyageClient
from .worker import run_forever


def _configure_logging() -> None:
    configure_logging()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m flashback.workers.embedding",
        description="Embedding worker for the Flashback agent service.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Drain the embedding queue (long-running).")

    bf = sub.add_parser(
        "backfill",
        help="Scan for NULL embeddings and enqueue them to SQS.",
    )
    bf.add_argument(
        "--record-type",
        choices=[*EMBEDDING_TARGETS.keys(), "all"],
        default="all",
    )
    bf.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be enqueued; do not actually send messages.",
    )
    return parser


def _cmd_run(cfg: Config) -> int:
    pool = make_pool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
    )
    voyage = VoyageClient(api_key=cfg.voyage_api_key)
    sqs = SQSClient(queue_url=cfg.embedding_queue_url, region_name=cfg.aws_region)
    try:
        run_forever(
            pool=pool,
            voyage=voyage,
            sqs=sqs,
            max_messages=cfg.sqs_max_messages,
            wait_seconds=cfg.sqs_wait_seconds,
        )
    finally:
        pool.close()
    return 0


def _cmd_backfill(cfg: Config, *, record_type: str, dry_run: bool) -> int:
    pool = make_pool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
    )
    sqs = SQSClient(queue_url=cfg.embedding_queue_url, region_name=cfg.aws_region)
    record_types = None if record_type == "all" else [record_type]
    try:
        results = backfill(
            pool=pool,
            sqs=sqs,
            embedding_model=cfg.embedding_model,
            embedding_model_version=cfg.embedding_model_version,
            record_types=record_types,
            dry_run=dry_run,
        )
    finally:
        pool.close()

    print(
        f"backfill summary "
        f"(model={cfg.embedding_model} version={cfg.embedding_model_version}, "
        f"dry_run={dry_run}):"
    )
    for r in results:
        print(f"  {r.record_type:<10} found={r.found:<5} enqueued={r.enqueued}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_parser().parse_args(argv)
    cfg = Config.from_env()

    if args.command == "run":
        return _cmd_run(cfg)
    if args.command == "backfill":
        return _cmd_backfill(cfg, record_type=args.record_type, dry_run=args.dry_run)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
