"""CLI entrypoint for the Trait Synthesizer worker.

Two subcommands::

    python -m flashback.workers.trait_synthesizer run
    python -m flashback.workers.trait_synthesizer run-once --person-id <uuid>

``run`` is the long-running drain loop; it requires
``TRAIT_SYNTHESIZER_QUEUE_URL`` to be set.

``run-once`` runs the same per-person logic synchronously without
touching SQS — useful for ops and end-to-end testing. It uses a
synthetic ``runonce-{person_id}-{ms}`` idempotency key so repeated
invocations against the same person each get their own row in
``processed_trait_syntheses``.
"""

from __future__ import annotations

import argparse
import sys

from flashback.config import TraitSynthesizerConfig
from flashback.db.connection import make_pool
from flashback.workers.extraction.sqs_client import EmbeddingJobSender

from .idempotency import make_runonce_key
from .runner import run_once
from .sqs_client import TraitSynthesizerSQSClient
from .synth_llm import SynthLLMConfig
from .worker import TraitSynthesizerWorker, _configure_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m flashback.workers.trait_synthesizer",
        description="Trait Synthesizer worker for the Flashback agent service.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Drain the trait_synthesizer queue (long-running).")
    once = sub.add_parser(
        "run-once",
        help="Run synthesis for a single person and exit.",
    )
    once.add_argument(
        "--person-id",
        required=True,
        help="UUID of the person to synthesize traits for.",
    )
    return parser


def _build_synth_cfg(cfg: TraitSynthesizerConfig) -> SynthLLMConfig:
    return SynthLLMConfig(
        provider=cfg.llm_trait_synth_provider,
        model=cfg.llm_trait_synth_model,
        timeout=cfg.llm_trait_synth_timeout_seconds,
        max_tokens=cfg.llm_trait_synth_max_tokens,
    )


def _cmd_run(cfg: TraitSynthesizerConfig) -> int:
    pool = make_pool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
    )
    sqs = TraitSynthesizerSQSClient(
        queue_url=cfg.trait_synthesizer_queue_url,
        region_name=cfg.aws_region,
    )
    embedding_sender = EmbeddingJobSender(
        queue_url=cfg.embedding_queue_url,
        region_name=cfg.aws_region,
    )
    worker = TraitSynthesizerWorker(
        db_pool=pool,
        sqs=sqs,
        embedding_sender=embedding_sender,
        synth_cfg=_build_synth_cfg(cfg),
        settings=cfg,
        embedding_model=cfg.embedding_model,
        embedding_model_version=cfg.embedding_model_version,
        sqs_wait_seconds=cfg.sqs_wait_seconds,
    )
    try:
        worker.run_forever()
    finally:
        pool.close()
    return 0


def _cmd_run_once(cfg: TraitSynthesizerConfig, *, person_id: str) -> int:
    pool = make_pool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
    )
    embedding_sender = EmbeddingJobSender(
        queue_url=cfg.embedding_queue_url,
        region_name=cfg.aws_region,
    )
    try:
        result = run_once(
            db_pool=pool,
            embedding_sender=embedding_sender,
            synth_cfg=_build_synth_cfg(cfg),
            settings=cfg,
            person_id=person_id,
            idempotency_key=make_runonce_key(person_id),
            embedding_model=cfg.embedding_model,
            embedding_model_version=cfg.embedding_model_version,
        )
    finally:
        pool.close()
    print(result.summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        cfg = TraitSynthesizerConfig.from_env(queue_required=True)
        return _cmd_run(cfg)
    if args.command == "run-once":
        cfg = TraitSynthesizerConfig.from_env(queue_required=False)
        return _cmd_run_once(cfg, person_id=args.person_id)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
