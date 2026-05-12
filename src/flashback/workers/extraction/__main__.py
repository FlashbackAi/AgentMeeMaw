"""
CLI entrypoint for the Extraction Worker.

One subcommand::

    python -m flashback.workers.extraction run

Configuration comes from environment variables (``ExtractionConfig``);
there are no config files. The worker is a long-running process and
returns only on SIGINT/SIGTERM.
"""

from __future__ import annotations

import argparse
import sys

from flashback.config import ExtractionConfig
from flashback.db.connection import make_pool

from .compatibility_llm import CompatibilityLLMConfig
from .extraction_llm import ExtractionLLMConfig
from .trait_merge_llm import TraitMergeLLMConfig
from .sqs_client import (
    ArtifactJobSender,
    EmbeddingJobSender,
    ExtractionSQSClient,
)
from .voyage_query import SyncVoyageQueryEmbedder
from .worker import ExtractionWorker, _configure_logging
from flashback.workers.thread_detector.sqs_client import ThreadDetectorJobSender


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m flashback.workers.extraction",
        description="Extraction worker for the Flashback agent service.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Drain the extraction queue (long-running).")
    return parser


def _cmd_run(cfg: ExtractionConfig) -> int:
    pool = make_pool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
    )
    sqs = ExtractionSQSClient(
        queue_url=cfg.extraction_queue_url,
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
    thread_detector_sender = ThreadDetectorJobSender(
        queue_url=cfg.thread_detector_queue_url,
        region_name=cfg.aws_region,
    )
    voyage = SyncVoyageQueryEmbedder(
        api_key=cfg.voyage_api_key,
        model=cfg.embedding_model,
        timeout=cfg.extraction_voyage_query_timeout_seconds,
    )

    worker = ExtractionWorker(
        db_pool=pool,
        sqs=sqs,
        embedding_sender=embedding_sender,
        artifact_sender=artifact_sender,
        thread_detector_sender=thread_detector_sender,
        voyage=voyage,
        extraction_cfg=ExtractionLLMConfig(
            provider=cfg.llm_extraction_provider,
            model=cfg.llm_extraction_model,
            timeout=cfg.llm_extraction_timeout_seconds,
            max_tokens=cfg.llm_extraction_max_tokens,
        ),
        compatibility_cfg=CompatibilityLLMConfig(
            provider=cfg.llm_compatibility_provider,
            model=cfg.llm_compatibility_model,
            timeout=cfg.llm_compatibility_timeout_seconds,
            max_tokens=cfg.llm_compatibility_max_tokens,
        ),
        trait_merge_cfg=TraitMergeLLMConfig(
            provider=cfg.llm_trait_merge_provider,
            model=cfg.llm_trait_merge_model,
            timeout=cfg.llm_trait_merge_timeout_seconds,
            max_tokens=cfg.llm_trait_merge_max_tokens,
        ),
        settings=cfg,
        embedding_model=cfg.embedding_model,
        embedding_model_version=cfg.embedding_model_version,
        refinement_distance_threshold=cfg.extraction_refinement_distance_threshold,
        refinement_candidate_limit=cfg.extraction_refinement_candidate_limit,
        sqs_wait_seconds=cfg.sqs_wait_seconds,
        thread_detector_cadence=cfg.thread_detector_cadence,
    )
    try:
        worker.run_forever()
    finally:
        pool.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_parser().parse_args(argv)
    cfg = ExtractionConfig.from_env()

    if args.command == "run":
        return _cmd_run(cfg)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
