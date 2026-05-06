"""CLI entrypoint for the Thread Detector worker.

One subcommand::

    python -m flashback.workers.thread_detector run

Configuration comes from environment variables (``ThreadDetectorConfig``);
there are no config files. The worker is a long-running process and
returns only on SIGINT/SIGTERM.
"""

from __future__ import annotations

import argparse
import sys

from flashback.config import ThreadDetectorConfig
from flashback.db.connection import make_pool
from flashback.workers.extraction.sqs_client import (
    ArtifactJobSender,
    EmbeddingJobSender,
)

from .naming_llm import NamingLLMConfig
from .p4_llm import P4LLMConfig
from .sqs_client import ThreadDetectorSQSClient
from .worker import ThreadDetectorWorker, _configure_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m flashback.workers.thread_detector",
        description="Thread Detector worker for the Flashback agent service.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "run", help="Drain the thread_detector queue (long-running)."
    )
    return parser


def _cmd_run(cfg: ThreadDetectorConfig) -> int:
    pool = make_pool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
    )
    sqs = ThreadDetectorSQSClient(
        queue_url=cfg.thread_detector_queue_url,
        region_name=cfg.aws_region,
    )
    embedding_sender = EmbeddingJobSender(
        queue_url=cfg.embedding_queue_url,
        region_name=cfg.aws_region,
    )
    artifact_sender = ArtifactJobSender(
        queue_url=cfg.artifact_queue_url,
        region_name=cfg.aws_region,
    )

    worker = ThreadDetectorWorker(
        db_pool=pool,
        sqs=sqs,
        embedding_sender=embedding_sender,
        artifact_sender=artifact_sender,
        naming_cfg=NamingLLMConfig(
            provider=cfg.llm_thread_naming_provider,
            model=cfg.llm_thread_naming_model,
            timeout=cfg.llm_thread_naming_timeout_seconds,
            max_tokens=cfg.llm_thread_naming_max_tokens,
        ),
        p4_cfg=P4LLMConfig(
            provider=cfg.llm_p4_provider,
            model=cfg.llm_p4_model,
            timeout=cfg.llm_p4_timeout_seconds,
            max_tokens=cfg.llm_p4_max_tokens,
        ),
        settings=cfg,
        embedding_model=cfg.embedding_model,
        embedding_model_version=cfg.embedding_model_version,
        min_cluster_size=cfg.thread_detector_min_cluster_size,
        existing_match_distance=cfg.thread_detector_existing_match_distance,
        thread_detector_cadence=cfg.thread_detector_cadence,
        sqs_wait_seconds=cfg.sqs_wait_seconds,
    )
    try:
        worker.run_forever()
    finally:
        pool.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_parser().parse_args(argv)
    cfg = ThreadDetectorConfig.from_env()

    if args.command == "run":
        return _cmd_run(cfg)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
