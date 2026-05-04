"""Per-cluster persistence (DB-touching, LLMs stubbed)."""

from __future__ import annotations

import math

import numpy as np

from flashback.workers.thread_detector import naming_llm as naming_mod
from flashback.workers.thread_detector import p4_llm as p4_mod
from flashback.workers.thread_detector.persistence import (
    fetch_clusterable_moments,
    process_cluster,
)
from flashback.workers.thread_detector.schema import Cluster, ClusterableMoment

from tests.workers.thread_detector.conftest import (
    StubArtifactSender,
    StubEmbeddingSender,
    queued_call_with_tool,
)
from tests.workers.thread_detector.fixtures.sample_clusters import (
    themed_embedding,
)

EMB_DIM = 1024
MODEL = "voyage-3-large"
VERSION = "2025-01-07"


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


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
                       narrative_embedding, embedding_model,
                       embedding_model_version)
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


def _build_cluster(moments: list[ClusterableMoment]) -> Cluster:
    arr = np.asarray([m.embedding for m in moments], dtype=np.float64)
    centroid = arr.mean(axis=0)
    n = float(np.linalg.norm(centroid))
    if n > 0:
        centroid = centroid / n
    return Cluster(
        member_moment_ids=[m.id for m in moments],
        member_embeddings=arr,
        centroid=centroid,
        confidence=0.85,
    )


def _stub_naming(monkeypatch, items):
    monkeypatch.setattr(
        naming_mod, "call_with_tool", queued_call_with_tool(items)
    )


def _stub_p4(monkeypatch, items):
    monkeypatch.setattr(
        p4_mod, "call_with_tool", queued_call_with_tool(items)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_new_thread_path_writes_full_graph(
    db_pool, make_person, monkeypatch, stub_naming_cfg, stub_p4_cfg, stub_settings
):
    person_id = make_person("Dad New")
    moment_dicts = [
        {
            "id": _seed_moment(
                db_pool,
                person_id=person_id,
                title=f"Cabin {i}",
                narrative=f"At the cabin {i}",
                embedding=themed_embedding(theme_index=0, seed=i, noise=0.02),
            ),
            "embedding": themed_embedding(theme_index=0, seed=i, noise=0.02),
            "title": f"Cabin {i}",
            "narrative": f"At the cabin {i}",
        }
        for i in range(3)
    ]
    moments = [
        ClusterableMoment(
            id=d["id"], title=d["title"], narrative=d["narrative"],
            embedding=d["embedding"],
        )
        for d in moment_dicts
    ]
    cluster = _build_cluster(moments)

    _stub_naming(
        monkeypatch,
        [
            {
                "coherent": True,
                "reasoning": "Same arc.",
                "name": "Cabin summers",
                "description": "Summers at the lake cabin.",
                "generation_prompt": "A cabin in golden summer light.",
            }
        ],
    )
    _stub_p4(
        monkeypatch,
        [
            {
                "questions": [
                    {
                        "text": "What did the cabin look like inside?",
                        "themes": ["place", "cabin"],
                    }
                ],
                "reasoning": "Surface sensory detail.",
            }
        ],
    )

    embedding_sender = StubEmbeddingSender()
    artifact_sender = StubArtifactSender()

    outcome = process_cluster(
        db_pool=db_pool,
        cluster=cluster,
        member_moments=moments,
        person_id=person_id,
        person_name="Dad",
        naming_cfg=stub_naming_cfg,
        p4_cfg=stub_p4_cfg,
        settings=stub_settings,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
        distance_threshold=0.4,
        embedding_job_pusher=embedding_sender.send,
        artifact_job_pusher=artifact_sender.send,
    )

    assert outcome.thread_was_created is True
    assert outcome.matched_existing is False
    assert outcome.incoherent is False
    assert outcome.thread_id is not None
    assert outcome.new_evidences_edge_count == 3
    assert len(outcome.questions_inserted) == 1

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, description, source, confidence "
                "FROM threads WHERE id=%s",
                (outcome.thread_id,),
            )
            name, description, source, confidence = cur.fetchone()
            assert name == "Cabin summers"
            assert source == "auto-detected"
            assert math.isclose(float(confidence), 0.85, abs_tol=1e-5)

            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE from_kind='moment' AND to_kind='thread'
                   AND to_id=%s AND edge_type='evidences'
                """,
                (outcome.thread_id,),
            )
            assert cur.fetchone()[0] == 3

            cur.execute(
                """
                SELECT count(*) FROM questions
                 WHERE person_id=%s AND source='thread_deepen'
                """,
                (person_id,),
            )
            assert cur.fetchone()[0] == 1

            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE from_kind='question' AND to_kind='thread'
                   AND to_id=%s AND edge_type='motivated_by'
                """,
                (outcome.thread_id,),
            )
            assert cur.fetchone()[0] == 1

    # Post-commit pushes: 1 thread embedding + 1 question embedding = 2.
    record_types = sorted(p["record_type"] for p in embedding_sender.sent)
    assert record_types == ["question", "thread"]
    # Artifact: 1 thread image.
    assert len(artifact_sender.sent) == 1
    assert artifact_sender.sent[0]["record_type"] == "thread"
    assert artifact_sender.sent[0]["artifact_kind"] == "image"


def test_existing_thread_match_path_does_not_create_thread(
    db_pool, make_person, monkeypatch, stub_naming_cfg, stub_p4_cfg, stub_settings
):
    person_id = make_person("Dad Match")

    # Seed moments + an existing thread whose embedding is close to the
    # cluster centroid.
    moment_data = [
        themed_embedding(theme_index=0, seed=i, noise=0.02) for i in range(3)
    ]
    moments = [
        ClusterableMoment(
            id=_seed_moment(
                db_pool,
                person_id=person_id,
                title=f"Cabin {i}",
                narrative=f"narrative {i}",
                embedding=moment_data[i],
            ),
            title=f"Cabin {i}",
            narrative=f"narrative {i}",
            embedding=moment_data[i],
        )
        for i in range(3)
    ]
    cluster = _build_cluster(moments)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO threads
                      (person_id, name, description,
                       description_embedding, embedding_model,
                       embedding_model_version)
                VALUES (%s, %s, %s, %s::vector, %s, %s)
                RETURNING id::text
                """,
                (
                    person_id,
                    "Existing cabin thread",
                    "An older description",
                    cluster.centroid.tolist(),
                    MODEL,
                    VERSION,
                ),
            )
            existing_thread_id = cur.fetchone()[0]
            conn.commit()

    # Naming should NOT be called on the match path. Pre-load only P4.
    _stub_naming(monkeypatch, [])
    _stub_p4(
        monkeypatch,
        [
            {
                "questions": [
                    {"text": "What seasons did you visit?", "themes": ["era"]}
                ],
                "reasoning": "Open up time anchors.",
            }
        ],
    )

    embedding_sender = StubEmbeddingSender()
    artifact_sender = StubArtifactSender()

    outcome = process_cluster(
        db_pool=db_pool,
        cluster=cluster,
        member_moments=moments,
        person_id=person_id,
        person_name="Dad",
        naming_cfg=stub_naming_cfg,
        p4_cfg=stub_p4_cfg,
        settings=stub_settings,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
        distance_threshold=0.4,
        embedding_job_pusher=embedding_sender.send,
        artifact_job_pusher=artifact_sender.send,
    )

    assert outcome.matched_existing is True
    assert outcome.thread_was_created is False
    assert outcome.thread_id == existing_thread_id
    assert outcome.new_evidences_edge_count == 3
    assert len(outcome.questions_inserted) == 1

    # No new thread row written, so no thread embedding/artifact pushes.
    record_types = [p["record_type"] for p in embedding_sender.sent]
    assert "thread" not in record_types
    assert artifact_sender.sent == []

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM threads WHERE person_id=%s",
                (person_id,),
            )
            # Still exactly one thread row.
            assert cur.fetchone()[0] == 1


def test_incoherent_naming_rolls_back(
    db_pool, make_person, monkeypatch, stub_naming_cfg, stub_p4_cfg, stub_settings
):
    person_id = make_person("Dad Inco")

    moment_data = [
        themed_embedding(theme_index=0, seed=i, noise=0.02) for i in range(3)
    ]
    moments = [
        ClusterableMoment(
            id=_seed_moment(
                db_pool,
                person_id=person_id,
                title=f"x{i}",
                narrative=f"y{i}",
                embedding=moment_data[i],
            ),
            title=f"x{i}",
            narrative=f"y{i}",
            embedding=moment_data[i],
        )
        for i in range(3)
    ]
    cluster = _build_cluster(moments)

    _stub_naming(
        monkeypatch,
        [{"coherent": False, "reasoning": "noisy"}],
    )
    # P4 must NOT be called.
    _stub_p4(monkeypatch, [])

    embedding_sender = StubEmbeddingSender()
    artifact_sender = StubArtifactSender()

    outcome = process_cluster(
        db_pool=db_pool,
        cluster=cluster,
        member_moments=moments,
        person_id=person_id,
        person_name="Dad",
        naming_cfg=stub_naming_cfg,
        p4_cfg=stub_p4_cfg,
        settings=stub_settings,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
        distance_threshold=0.4,
        embedding_job_pusher=embedding_sender.send,
        artifact_job_pusher=artifact_sender.send,
    )

    assert outcome.incoherent is True
    assert outcome.thread_was_created is False
    assert outcome.thread_id is None
    assert outcome.questions_inserted == []
    assert embedding_sender.sent == []
    assert artifact_sender.sent == []

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM threads WHERE person_id=%s",
                (person_id,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE edge_type='evidences'
                   AND from_kind='moment'
                   AND from_id IN (
                       SELECT id FROM moments WHERE person_id=%s
                   )
                """,
                (person_id,),
            )
            assert cur.fetchone()[0] == 0


def test_evidences_edges_idempotent_on_rerun(
    db_pool, make_person, monkeypatch, stub_naming_cfg, stub_p4_cfg, stub_settings
):
    """ON CONFLICT DO NOTHING: re-running the same cluster doesn't dup edges."""
    person_id = make_person("Dad Idem")
    moment_data = [
        themed_embedding(theme_index=0, seed=i, noise=0.02) for i in range(3)
    ]
    moments = [
        ClusterableMoment(
            id=_seed_moment(
                db_pool,
                person_id=person_id,
                title=f"x{i}",
                narrative=f"y{i}",
                embedding=moment_data[i],
            ),
            title=f"x{i}",
            narrative=f"y{i}",
            embedding=moment_data[i],
        )
        for i in range(3)
    ]
    cluster = _build_cluster(moments)

    # First run: name new thread, write evidences, P4 questions.
    _stub_naming(
        monkeypatch,
        [
            {
                "coherent": True,
                "reasoning": "ok",
                "name": "First",
                "description": "Description",
                "generation_prompt": "A scene.",
            }
        ],
    )
    _stub_p4(
        monkeypatch,
        [
            {
                "questions": [{"text": "Q?", "themes": ["a"]}],
                "reasoning": "ok",
            }
        ],
    )

    embedding_sender = StubEmbeddingSender()
    artifact_sender = StubArtifactSender()

    first = process_cluster(
        db_pool=db_pool,
        cluster=cluster,
        member_moments=moments,
        person_id=person_id,
        person_name="Dad",
        naming_cfg=stub_naming_cfg,
        p4_cfg=stub_p4_cfg,
        settings=stub_settings,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
        distance_threshold=0.4,
        embedding_job_pusher=embedding_sender.send,
        artifact_job_pusher=artifact_sender.send,
    )
    assert first.thread_was_created is True

    # Second run on the same cluster — but the existing thread now has
    # NO embedding (the embedding worker hasn't run yet), so the matching
    # query returns zero rows. The detector treats this as create-new
    # again and the LLMs are called once more. Evidences edges from the
    # first run still ON CONFLICT DO NOTHING for the new thread, but
    # since the new thread has a fresh id, the edges are inserted again.
    #
    # The narrower "ON CONFLICT DO NOTHING" guarantee is only meaningful
    # when the SAME thread id is presented with the SAME moment ids; we
    # exercise that explicitly by feeding the existing thread id back
    # through a follow-up call. Easiest route: insert the description
    # embedding manually so the match path picks it up.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE threads
                   SET description_embedding = %s::vector,
                       embedding_model = %s,
                       embedding_model_version = %s
                 WHERE id = %s
                """,
                (cluster.centroid.tolist(), MODEL, VERSION, first.thread_id),
            )
            conn.commit()

    _stub_naming(monkeypatch, [])
    _stub_p4(
        monkeypatch,
        [
            {
                "questions": [{"text": "Q2?", "themes": ["b"]}],
                "reasoning": "ok",
            }
        ],
    )
    second = process_cluster(
        db_pool=db_pool,
        cluster=cluster,
        member_moments=moments,
        person_id=person_id,
        person_name="Dad",
        naming_cfg=stub_naming_cfg,
        p4_cfg=stub_p4_cfg,
        settings=stub_settings,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
        distance_threshold=0.4,
        embedding_job_pusher=embedding_sender.send,
        artifact_job_pusher=artifact_sender.send,
    )
    assert second.matched_existing is True
    assert second.thread_id == first.thread_id
    # All three evidences edges already exist → no NEW edges inserted.
    assert second.new_evidences_edge_count == 0

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE from_kind='moment' AND to_kind='thread'
                   AND to_id=%s AND edge_type='evidences'
                """,
                (first.thread_id,),
            )
            assert cur.fetchone()[0] == 3


def test_fetch_clusterable_moments_filters_to_active_with_embeddings(
    db_pool, make_person
):
    person_id = make_person("Fetch A")
    e = themed_embedding(theme_index=0, seed=1, noise=0.02)

    # Active with embedding → kept.
    keep = _seed_moment(
        db_pool,
        person_id=person_id,
        title="keep",
        narrative="x",
        embedding=e,
    )
    # Active without embedding → dropped.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments (person_id, title, narrative)
                VALUES (%s, 'noemb', 'x')
                RETURNING id::text
                """,
                (person_id,),
            )
            cur.fetchone()
            conn.commit()
    # Stale embedding model → dropped.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments
                      (person_id, title, narrative,
                       narrative_embedding, embedding_model, embedding_model_version)
                VALUES (%s, 'stale', 'x', %s::vector, %s, %s)
                RETURNING id::text
                """,
                (person_id, e, "voyage-2", "old"),
            )
            conn.commit()

    rows = fetch_clusterable_moments(
        db_pool,
        person_id=person_id,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )

    assert [r.id for r in rows] == [keep]
