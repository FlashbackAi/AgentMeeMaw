"""CLI entrypoint for Question Producers P2/P3/P5."""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from flashback.config import ProducerConfig
from flashback.db.connection import make_pool
from flashback.workers.extraction.sqs_client import EmbeddingJobSender

from .idempotency import make_runonce_key
from .runner import run_once
from .sqs_client import ProducerSQSClient
from .worker import ProducerWorker, _configure_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m flashback.workers.producers",
        description="Question Producers P2/P3/P5 for Flashback.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "run-per-session",
        help="Drain the per-session producers queue (P2 only).",
    )
    sub.add_parser(
        "run-weekly",
        help="Drain the weekly producers queue (P3 and P5).",
    )
    once = sub.add_parser(
        "run-once",
        help="Run one producer synchronously for one person.",
    )
    once.add_argument("--producer", choices=["P2", "P3", "P5"], required=True)
    once.add_argument("--person-id", required=True)
    return parser


def _make_pool(cfg: ProducerConfig):
    return make_pool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
    )


def _make_embedding_sender(cfg: ProducerConfig) -> EmbeddingJobSender:
    return EmbeddingJobSender(
        queue_url=cfg.embedding_queue_url,
        region_name=cfg.aws_region,
    )


def _run_loop(cfg: ProducerConfig, *, queue_url: str, allowed: frozenset[str]) -> int:
    pool = _make_pool(cfg)
    worker = ProducerWorker(
        db_pool=pool,
        sqs=ProducerSQSClient(queue_url=queue_url, region_name=cfg.aws_region),
        embedding_sender=_make_embedding_sender(cfg),
        settings=cfg,
        allowed_producers=allowed,
        embedding_model=cfg.embedding_model,
        embedding_model_version=cfg.embedding_model_version,
        sqs_wait_seconds=cfg.sqs_wait_seconds,
    )
    try:
        worker.run_forever()
    finally:
        pool.close()
    return 0


def _run_once(cfg: ProducerConfig, *, producer: str, person_id: str) -> int:
    pool = _make_pool(cfg)
    try:
        result = asyncio.run(
            run_once(
                db_pool=pool,
                embedding_sender=_make_embedding_sender(cfg),
                settings=cfg,
                producer_tag=producer,
                person_id=UUID(person_id),
                idempotency_key=make_runonce_key(producer, person_id),
                embedding_model=cfg.embedding_model,
                embedding_model_version=cfg.embedding_model_version,
            )
        )
    finally:
        pool.close()
    print(result.summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_parser().parse_args(argv)
    if args.command == "run-per-session":
        cfg = ProducerConfig.from_env(queue_required="per-session")
        return _run_loop(
            cfg,
            queue_url=cfg.producers_per_session_queue_url,
            allowed=frozenset({"P2"}),
        )
    if args.command == "run-weekly":
        cfg = ProducerConfig.from_env(queue_required="weekly")
        return _run_loop(
            cfg,
            queue_url=cfg.producers_weekly_queue_url,
            allowed=frozenset({"P3", "P5"}),
        )
    if args.command == "run-once":
        cfg = ProducerConfig.from_env(queue_required=None)
        return _run_once(cfg, producer=args.producer, person_id=args.person_id)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())

