"""End-to-end Thread Detector worker tests.

DB-touching; LLMs and SQS are stubbed. Exercises the full message-loop
shape: trigger re-validation, clusterable-moment fetch, HDBSCAN,
per-cluster persistence + post-commit pushes, and the
``moments_at_last_thread_run`` baseline update.
"""

from __future__ import annotations

import numpy as np

from flashback.workers.thread_detector import naming_llm as naming_mod
from flashback.workers.thread_detector import p4_llm as p4_mod
from flashback.workers.thread_detector import persistence as persistence_mod
from flashback.workers.thread_detector.worker import ThreadDetectorWorker

from tests.workers.thread_detector.conftest import (
    StubArtifactSender,
    StubEmbeddingSender,
    StubThreadDetectorSQSClient,
    make_thread_detector_message,
    queued_call_with_tool,
)
from tests.workers.thread_detector.fixtures.sample_clusters import (
    make_themed_moments,
    themed_embedding,
)

MODEL = "voyage-3-large"
VERSION = "2025-01-07"


def _seed_moment(
    db_pool,
    *,
    person_id: str,
    title: str,
    narrative: str,
    embedding: list[float],
    status: str = "active",
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments
                      (person_id, title, narrative, status,
                       narrative_embedding, embedding_model, embedding_model_version)
                VALUES (%s, %s, %s, %s, %s::vector, %s, %s)
                RETURNING id::text
                """,
                (
                    person_id,
                    title,
                    narrative,
                    status,
                    embedding,
                    MODEL,
                    VERSION,
                ),
            )
            mid = cur.fetchone()[0]
            conn.commit()
    return mid


def _set_last_count(db_pool, person_id: str, value: int) -> None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE persons SET moments_at_last_thread_run=%s WHERE id=%s",
                (value, person_id),
            )
            conn.commit()


def _build_worker(
    *,
    db_pool,
    stub_naming_cfg,
    stub_p4_cfg,
    stub_settings,
) -> ThreadDetectorWorker:
    return ThreadDetectorWorker(
        db_pool=db_pool,
        sqs=StubThreadDetectorSQSClient(),
        embedding_sender=StubEmbeddingSender(),
        artifact_sender=StubArtifactSender(),
        naming_cfg=stub_naming_cfg,
        p4_cfg=stub_p4_cfg,
        settings=stub_settings,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
        min_cluster_size=3,
        existing_match_distance=0.4,
    )


def _seed_two_clusters(db_pool, person_id: str) -> None:
    """18 moments split across 2 themes (cabin / workshop)."""
    cabin = make_themed_moments(theme_index=0, n=9, seed_offset=0)
    workshop = make_themed_moments(theme_index=5, n=9, seed_offset=100)
    for d in cabin + workshop:
        _seed_moment(
            db_pool,
            person_id=person_id,
            title=d["title"],
            narrative=d["narrative"],
            embedding=d["embedding"],
        )


def test_two_clusters_create_two_threads_end_to_end(
    db_pool,
    make_person,
    monkeypatch,
    stub_naming_cfg,
    stub_p4_cfg,
    stub_settings,
):
    person_id = make_person("Dad E2E")
    _seed_two_clusters(db_pool, person_id)

    # Two new clusters → two naming calls + two P4 calls.
    monkeypatch.setattr(
        naming_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                {
                    "coherent": True,
                    "reasoning": "ok",
                    "name": "Cabin summers",
                    "description": "Summers at the cabin.",
                    "generation_prompt": "A wooden cabin in summer light.",
                },
                {
                    "coherent": True,
                    "reasoning": "ok",
                    "name": "His workshop",
                    "description": "Hours in the workshop.",
                    "generation_prompt": "A workshop bench bathed in lamplight.",
                },
            ]
        ),
    )
    monkeypatch.setattr(
        p4_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                {
                    "questions": [
                        {"text": "What did the cabin smell like?", "themes": ["sensory"]}
                    ],
                    "reasoning": "open up sensory",
                },
                {
                    "questions": [
                        {"text": "What tools did he use most?", "themes": ["object"]}
                    ],
                    "reasoning": "concrete detail",
                },
            ]
        ),
    )

    worker = _build_worker(
        db_pool=db_pool,
        stub_naming_cfg=stub_naming_cfg,
        stub_p4_cfg=stub_p4_cfg,
        stub_settings=stub_settings,
    )
    msg = make_thread_detector_message(person_id=person_id)

    outcomes = worker.process_message(msg)

    assert len(outcomes) == 2
    assert all(o.thread_was_created for o in outcomes)
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM threads WHERE person_id=%s", (person_id,)
            )
            assert cur.fetchone()[0] == 2
            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE from_kind='moment' AND to_kind='thread'
                   AND edge_type='evidences'
                   AND from_id IN (
                       SELECT id FROM moments WHERE person_id=%s
                   )
                """,
                (person_id,),
            )
            # 9 moments per cluster x 2 clusters = 18.
            assert cur.fetchone()[0] == 18
            cur.execute(
                """
                SELECT count(*) FROM questions
                 WHERE person_id=%s AND source='thread_deepen'
                """,
                (person_id,),
            )
            assert cur.fetchone()[0] == 2
            cur.execute(
                "SELECT moments_at_last_thread_run FROM persons WHERE id=%s",
                (person_id,),
            )
            # 18 active moments total.
            assert cur.fetchone()[0] == 18

    # Post-commit pushes: 2 thread embeddings + 2 question embeddings = 4.
    embedding_sender = worker.embedding_sender  # type: ignore[attr-defined]
    record_types = sorted(p["record_type"] for p in embedding_sender.sent)
    assert record_types == ["question", "question", "thread", "thread"]

    artifact_sender = worker.artifact_sender  # type: ignore[attr-defined]
    artifact_kinds = sorted(p["artifact_kind"] for p in artifact_sender.sent)
    assert artifact_kinds == ["image", "image"]


def test_stale_trigger_acks_without_writes(
    db_pool,
    make_person,
    monkeypatch,
    stub_naming_cfg,
    stub_p4_cfg,
    stub_settings,
):
    person_id = make_person("Dad Stale")
    _seed_two_clusters(db_pool, person_id)
    # Move the baseline forward so delta = 0 → trigger no longer valid.
    _set_last_count(db_pool, person_id, 18)

    # No LLM stubs; fail loudly if either is invoked.
    def _fail(**kwargs):
        raise AssertionError("LLM should not be called on stale trigger")

    async def _fail_async(**kwargs):
        _fail(**kwargs)

    monkeypatch.setattr(naming_mod, "call_with_tool", _fail_async)
    monkeypatch.setattr(p4_mod, "call_with_tool", _fail_async)

    worker = _build_worker(
        db_pool=db_pool,
        stub_naming_cfg=stub_naming_cfg,
        stub_p4_cfg=stub_p4_cfg,
        stub_settings=stub_settings,
    )
    msg = make_thread_detector_message(person_id=person_id)
    outcomes = worker.process_message(msg)

    assert outcomes == []
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM threads WHERE person_id=%s", (person_id,)
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT moments_at_last_thread_run FROM persons WHERE id=%s",
                (person_id,),
            )
            # Untouched.
            assert cur.fetchone()[0] == 18


def test_single_cluster_failure_does_not_abort_run(
    db_pool,
    make_person,
    monkeypatch,
    stub_naming_cfg,
    stub_p4_cfg,
    stub_settings,
):
    """One cluster's failure must leave the others' writes intact."""
    person_id = make_person("Dad Mid")
    _seed_two_clusters(db_pool, person_id)

    # Patch process_cluster: first cluster raises, second succeeds.
    real_process_cluster = persistence_mod.process_cluster
    call_count = {"n": 0}

    def flaky_process_cluster(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated cluster A failure")
        return real_process_cluster(**kwargs)

    # Worker imports process_cluster from .persistence inside worker.py
    # via a from-import, so patch the worker module's reference.
    from flashback.workers.thread_detector import worker as worker_mod
    monkeypatch.setattr(worker_mod, "process_cluster", flaky_process_cluster)

    monkeypatch.setattr(
        naming_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                {
                    "coherent": True,
                    "reasoning": "ok",
                    "name": "Survivor thread",
                    "description": "Some description.",
                    "generation_prompt": "A scene.",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        p4_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                {
                    "questions": [{"text": "Q?", "themes": ["a"]}],
                    "reasoning": "ok",
                }
            ]
        ),
    )

    worker = _build_worker(
        db_pool=db_pool,
        stub_naming_cfg=stub_naming_cfg,
        stub_p4_cfg=stub_p4_cfg,
        stub_settings=stub_settings,
    )
    msg = make_thread_detector_message(person_id=person_id)
    outcomes = worker.process_message(msg)

    # Only the second cluster survived.
    assert len(outcomes) == 1
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM threads WHERE person_id=%s", (person_id,)
            )
            assert cur.fetchone()[0] == 1
            # Baseline was updated because at least one cluster succeeded.
            cur.execute(
                "SELECT moments_at_last_thread_run FROM persons WHERE id=%s",
                (person_id,),
            )
            assert cur.fetchone()[0] == 18


def test_run_failure_before_cluster_writes_does_not_ack(
    db_pool,
    make_person,
    monkeypatch,
    stub_naming_cfg,
    stub_p4_cfg,
    stub_settings,
):
    """A run-level failure (e.g., DB issue during fetch) leaves the message
    in flight; SQS will redrive."""
    person_id = make_person("Dad Catastrophe")
    _seed_two_clusters(db_pool, person_id)

    from flashback.workers.thread_detector import worker as worker_mod

    def _boom(**kwargs):
        raise RuntimeError("DB blew up")

    monkeypatch.setattr(worker_mod, "fetch_clusterable_moments", _boom)

    worker = _build_worker(
        db_pool=db_pool,
        stub_naming_cfg=stub_naming_cfg,
        stub_p4_cfg=stub_p4_cfg,
        stub_settings=stub_settings,
    )
    msg = make_thread_detector_message(person_id=person_id)
    outcomes = worker.process_message(msg)

    assert outcomes == []
    # NOT acked — SQS will redeliver.
    assert worker.sqs.deleted == []  # type: ignore[attr-defined]


def test_too_few_moments_acks_and_updates_baseline(
    db_pool,
    make_person,
    stub_naming_cfg,
    stub_p4_cfg,
    stub_settings,
):
    person_id = make_person("Dad Few")

    # Two moments only (below min_cluster_size=3).
    for i in range(2):
        _seed_moment(
            db_pool,
            person_id=person_id,
            title=f"t{i}",
            narrative=f"n{i}",
            embedding=themed_embedding(theme_index=0, seed=i),
        )

    worker = _build_worker(
        db_pool=db_pool,
        stub_naming_cfg=stub_naming_cfg,
        stub_p4_cfg=stub_p4_cfg,
        stub_settings=stub_settings,
    )
    msg = make_thread_detector_message(person_id=person_id)
    # The trigger gate (>=15) is what really protects this; bypass it by
    # forcing the trigger valid via 15 unembedded moments.
    for i in range(15):
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO moments (person_id, title, narrative)
                       VALUES (%s, 'noemb', 'x')""",
                    (person_id,),
                )
                conn.commit()

    outcomes = worker.process_message(msg)

    assert outcomes == []
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT moments_at_last_thread_run FROM persons WHERE id=%s",
                (person_id,),
            )
            # 17 active moments (2 with embeddings + 15 without).
            assert cur.fetchone()[0] == 17
