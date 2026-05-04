"""Match-or-create lookups against existing threads (DB-touching)."""

from __future__ import annotations

import numpy as np

from flashback.workers.thread_detector.matching import (
    fetch_thread_snapshot,
    match_existing_thread,
)
from flashback.workers.thread_detector.schema import Cluster

EMB_DIM = 1024
MODEL = "voyage-3-large"
VERSION = "2025-01-07"


def _unit_vec(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    raw = rng.standard_normal(EMB_DIM)
    return (raw / np.linalg.norm(raw)).tolist()


def _make_cluster(centroid: list[float]) -> Cluster:
    arr = np.asarray(centroid, dtype=np.float64)
    arr = arr / np.linalg.norm(arr)
    return Cluster(
        member_moment_ids=["m1", "m2", "m3"],
        member_embeddings=np.tile(arr, (3, 1)),
        centroid=arr,
        confidence=0.9,
    )


def _insert_thread(
    db_pool,
    *,
    person_id: str,
    name: str,
    description: str,
    embedding: list[float] | None,
    model: str = MODEL,
    version: str = VERSION,
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            if embedding is None:
                cur.execute(
                    """
                    INSERT INTO threads (person_id, name, description)
                    VALUES (%s, %s, %s)
                    RETURNING id::text
                    """,
                    (person_id, name, description),
                )
            else:
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
                        name,
                        description,
                        embedding,
                        model,
                        version,
                    ),
                )
            tid = cur.fetchone()[0]
            conn.commit()
    return tid


def test_no_existing_threads_returns_empty(db_pool, make_person):
    person_id = make_person("Match A")
    cluster = _make_cluster(_unit_vec(1))

    result = match_existing_thread(
        db_pool,
        cluster=cluster,
        person_id=person_id,
        distance_threshold=0.4,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )

    assert result.existing_thread_id is None
    assert result.existing_thread_distance is None
    assert result.is_match is False


def test_close_existing_thread_returns_id(db_pool, make_person):
    person_id = make_person("Match B")
    centroid = _unit_vec(7)
    # Use the EXACT same vector for the existing thread → distance ~ 0.
    tid = _insert_thread(
        db_pool,
        person_id=person_id,
        name="Cabin summers",
        description="A thread",
        embedding=centroid,
    )
    cluster = _make_cluster(centroid)

    result = match_existing_thread(
        db_pool,
        cluster=cluster,
        person_id=person_id,
        distance_threshold=0.4,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )

    assert result.existing_thread_id == tid
    assert result.existing_thread_distance is not None
    assert result.existing_thread_distance < 0.01
    assert result.is_match is True


def test_far_existing_thread_returns_none(db_pool, make_person):
    person_id = make_person("Match C")
    # Existing thread vector vs cluster centroid: orthogonal → cosine
    # distance = 1.0, well above the 0.4 threshold.
    far_vec = [0.0] * EMB_DIM
    far_vec[0] = 1.0
    cluster_vec = [0.0] * EMB_DIM
    cluster_vec[1] = 1.0

    _insert_thread(
        db_pool,
        person_id=person_id,
        name="other",
        description="other",
        embedding=far_vec,
    )
    cluster = _make_cluster(cluster_vec)

    result = match_existing_thread(
        db_pool,
        cluster=cluster,
        person_id=person_id,
        distance_threshold=0.4,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )

    assert result.existing_thread_id is None
    assert result.existing_thread_distance is not None
    assert result.existing_thread_distance > 0.4
    assert result.is_match is False


def test_mixed_embedding_models_filtered_out(db_pool, make_person):
    """Per invariant #3, only current-model threads are considered."""
    person_id = make_person("Match D")
    centroid = _unit_vec(11)

    # Stale-model thread that would otherwise match perfectly.
    _insert_thread(
        db_pool,
        person_id=person_id,
        name="stale model",
        description="x",
        embedding=centroid,
        model="voyage-2",
        version="2024-01-01",
    )
    cluster = _make_cluster(centroid)

    result = match_existing_thread(
        db_pool,
        cluster=cluster,
        person_id=person_id,
        distance_threshold=0.4,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )

    assert result.existing_thread_id is None


def test_other_person_threads_are_not_considered(db_pool, make_person):
    """Per invariant #2, similarity searches scope to the person."""
    other_id = make_person("Other person")
    person_id = make_person("Match E")

    centroid = _unit_vec(13)
    _insert_thread(
        db_pool,
        person_id=other_id,
        name="other person's thread",
        description="x",
        embedding=centroid,
    )
    cluster = _make_cluster(centroid)

    result = match_existing_thread(
        db_pool,
        cluster=cluster,
        person_id=person_id,
        distance_threshold=0.4,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )

    assert result.existing_thread_id is None


def test_fetch_thread_snapshot_returns_name_and_description(
    db_pool, make_person
):
    person_id = make_person("Match F")
    tid = _insert_thread(
        db_pool,
        person_id=person_id,
        name="Sunday dinners",
        description="Family meals on Sundays",
        embedding=None,
    )

    snap = fetch_thread_snapshot(db_pool, thread_id=tid)

    assert snap.id == tid
    assert snap.name == "Sunday dinners"
    assert snap.description == "Family meals on Sundays"
