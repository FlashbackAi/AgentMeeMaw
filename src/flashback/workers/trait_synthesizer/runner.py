"""Per-person execution for the Trait Synthesizer.

The :func:`run_once` entry point is shared by:

* The SQS drain loop (worker.py), which calls it with the SQS
  ``MessageId`` as ``idempotency_key``.
* The CLI ``run-once --person-id <uuid>`` subcommand, which calls it
  with a ``runonce-{person_id}-{ms}`` synthetic key (best-effort
  idempotency for ad-hoc testing).

Sequence:

  1. Idempotency check (read-only). If already processed, return
     :class:`RunResult.skipped`.
  2. Build context (DB read).
  3. Single LLM call (raises ``LLMTimeout`` / ``LLMError`` on failure).
  4. Per-person transaction: persist decisions + idempotency row.
  5. Post-commit: push embedding jobs for newly inserted traits.

Failure modes propagate to the caller as exceptions; :mod:`worker`
maps them to ack / no-ack semantics.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from flashback.workers.extraction.sqs_client import EmbeddingJobSender

from .context import build_context
from .idempotency import is_processed
from .persistence import (
    PersistResult,
    persist_synthesis,
    push_new_trait_embeddings,
)
from .schema import TraitSynthesisResult
from .synth_llm import SynthLLMConfig, synthesize

log = structlog.get_logger("flashback.workers.trait_synthesizer.runner")


@dataclass
class RunResult:
    """Outcome of one ``run_once`` invocation."""

    skipped: bool = False
    persist: PersistResult | None = None

    @classmethod
    def skip(cls) -> "RunResult":
        return cls(skipped=True, persist=None)

    @classmethod
    def from_persist(cls, persist: PersistResult) -> "RunResult":
        return cls(skipped=False, persist=persist)

    def summary(self) -> dict:
        if self.skipped:
            return {"skipped": True}
        assert self.persist is not None
        return {"skipped": False, **self.persist.summary()}


def run_once(
    *,
    db_pool,
    embedding_sender: EmbeddingJobSender,
    synth_cfg: SynthLLMConfig,
    settings,
    person_id: str,
    idempotency_key: str,
    embedding_model: str,
    embedding_model_version: str,
    contributor_display_name: str = "",
) -> RunResult:
    """Synthesize traits for a single person, end to end."""
    # 1. Idempotency check.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            if is_processed(cur, idempotency_key):
                log.info(
                    "trait_synthesizer.skipped_already_processed",
                    idempotency_key=idempotency_key,
                    person_id=person_id,
                )
                return RunResult.skip()

    # 2. Context.
    context = build_context(
        db_pool,
        person_id=person_id,
        contributor_display_name=contributor_display_name,
    )

    # 3. LLM call (may raise LLMTimeout / LLMError).
    synth_result: TraitSynthesisResult = synthesize(
        cfg=synth_cfg,
        settings=settings,
        context=context,
    )

    # 4. Per-person transaction.
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                persist = persist_synthesis(
                    cur,
                    person_id=person_id,
                    result=synth_result,
                    idempotency_key=idempotency_key,
                )

    log.info(
        "trait_synthesizer.persisted",
        person_id=person_id,
        idempotency_key=idempotency_key,
        **persist.summary(),
    )

    # 5. Post-commit: embedding pushes for new traits only.
    push_new_trait_embeddings(
        embedding_sender=embedding_sender,
        new_traits=persist.new_traits,
        embedding_model=embedding_model,
        embedding_model_version=embedding_model_version,
    )

    return RunResult.from_persist(persist)
