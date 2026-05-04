"""CLI entrypoint for the Profile Summary Generator worker.

Two subcommands::

    python -m flashback.workers.profile_summary run
    python -m flashback.workers.profile_summary run-once --person-id <uuid>

``run`` is the long-running drain loop; it requires
``PROFILE_SUMMARY_QUEUE_URL`` to be set.

``run-once`` runs the same per-person logic synchronously without
touching SQS — useful for ops and end-to-end testing. It uses a
synthetic ``runonce-{person_id}-{ms}`` idempotency key so repeated
invocations against the same person each get their own row in
``processed_profile_summaries`` (and produce a fresh overwrite of
``persons.profile_summary``).
"""

from __future__ import annotations

import argparse
import sys

from flashback.config import ProfileSummaryConfig
from flashback.db.connection import make_pool

from .idempotency import make_runonce_key
from .runner import run_once
from .sqs_client import ProfileSummarySQSClient
from .summary_llm import SummaryLLMConfig
from .worker import ProfileSummaryWorker, _configure_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m flashback.workers.profile_summary",
        description="Profile Summary Generator for the Flashback agent service.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Drain the profile_summary queue (long-running).")
    once = sub.add_parser(
        "run-once",
        help="Generate a profile summary for a single person and exit.",
    )
    once.add_argument(
        "--person-id",
        required=True,
        help="UUID of the person to summarize.",
    )
    return parser


def _build_summary_cfg(cfg: ProfileSummaryConfig) -> SummaryLLMConfig:
    return SummaryLLMConfig(
        provider=cfg.llm_profile_summary_provider,
        model=cfg.llm_profile_summary_model,
        timeout=cfg.llm_profile_summary_timeout_seconds,
        max_tokens=cfg.llm_profile_summary_max_tokens,
    )


def _cmd_run(cfg: ProfileSummaryConfig) -> int:
    pool = make_pool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
    )
    sqs = ProfileSummarySQSClient(
        queue_url=cfg.profile_summary_queue_url,
        region_name=cfg.aws_region,
    )
    worker = ProfileSummaryWorker(
        db_pool=pool,
        sqs=sqs,
        summary_cfg=_build_summary_cfg(cfg),
        settings=cfg,
        top_traits_max=cfg.profile_summary_top_traits_max,
        top_threads_max=cfg.profile_summary_top_threads_max,
        top_entities_max=cfg.profile_summary_top_entities_max,
        sqs_wait_seconds=cfg.sqs_wait_seconds,
    )
    try:
        worker.run_forever()
    finally:
        pool.close()
    return 0


def _cmd_run_once(cfg: ProfileSummaryConfig, *, person_id: str) -> int:
    pool = make_pool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
    )
    try:
        result = run_once(
            db_pool=pool,
            summary_cfg=_build_summary_cfg(cfg),
            settings=cfg,
            person_id=person_id,
            idempotency_key=make_runonce_key(person_id),
            top_traits_max=cfg.profile_summary_top_traits_max,
            top_threads_max=cfg.profile_summary_top_threads_max,
            top_entities_max=cfg.profile_summary_top_entities_max,
        )
    finally:
        pool.close()
    print(result.summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        cfg = ProfileSummaryConfig.from_env(queue_required=True)
        return _cmd_run(cfg)
    if args.command == "run-once":
        cfg = ProfileSummaryConfig.from_env(queue_required=False)
        return _cmd_run_once(cfg, person_id=args.person_id)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
