"""HDBSCAN clustering tests (pure compute — no DB)."""

from __future__ import annotations

import math

import numpy as np

from flashback.workers.thread_detector.clustering import (
    count_outliers,
    run_hdbscan,
)
from flashback.workers.thread_detector.schema import ClusterableMoment

from tests.workers.thread_detector.fixtures.sample_clusters import (
    make_themed_moments,
    themed_embedding,
)


def _to_moments(seed: list[dict]) -> list[ClusterableMoment]:
    return [
        ClusterableMoment(
            id=d["id"],
            title=d["title"],
            narrative=d["narrative"],
            embedding=d["embedding"],
        )
        for d in seed
    ]


def test_three_similar_moments_form_one_cluster():
    moments = _to_moments(make_themed_moments(theme_index=0, n=4))

    clusters = run_hdbscan(moments, min_cluster_size=3)

    assert len(clusters) == 1
    cluster = clusters[0]
    assert len(cluster.member_moment_ids) >= 3
    # Centroid is L2-normalized.
    assert math.isclose(float(np.linalg.norm(cluster.centroid)), 1.0, abs_tol=1e-6)
    assert 0.0 <= cluster.confidence <= 1.0


def test_two_separated_themes_form_two_clusters():
    seed_a = make_themed_moments(theme_index=0, n=4, seed_offset=0)
    seed_b = make_themed_moments(theme_index=5, n=4, seed_offset=100)
    moments = _to_moments(seed_a + seed_b)

    clusters = run_hdbscan(moments, min_cluster_size=3)

    assert len(clusters) == 2
    membership_a = {m["id"] for m in seed_a}
    membership_b = {m["id"] for m in seed_b}
    cluster_sets = [set(c.member_moment_ids) for c in clusters]
    assert any(s.issubset(membership_a) for s in cluster_sets)
    assert any(s.issubset(membership_b) for s in cluster_sets)


def test_below_min_cluster_size_returns_no_clusters():
    moments = _to_moments(make_themed_moments(theme_index=0, n=2))

    clusters = run_hdbscan(moments, min_cluster_size=3)

    assert clusters == []


def test_outliers_are_dropped():
    seed_a = make_themed_moments(theme_index=0, n=4, seed_offset=0)
    # Three lone outliers from far-apart themes.
    outliers = [
        {
            "id": f"out-{i}",
            "title": "lone",
            "narrative": "n",
            "embedding": themed_embedding(theme_index=10 + i, seed=999 + i),
        }
        for i in range(3)
    ]
    moments = _to_moments(seed_a + outliers)

    clusters = run_hdbscan(moments, min_cluster_size=3)

    in_cluster = {mid for c in clusters for mid in c.member_moment_ids}
    for o in outliers:
        assert o["id"] not in in_cluster
    assert count_outliers(moments, clusters) >= len(outliers)


def test_cosine_via_normalized_euclidean_groups_similar_vectors():
    """Vectors with high cosine similarity end up in the same cluster.

    Two unit vectors that point in nearly the same direction MUST
    cluster together under the workaround used in clustering.py.
    """
    base = themed_embedding(theme_index=0, seed=0, noise=0.005)
    near = themed_embedding(theme_index=0, seed=1, noise=0.005)
    near_2 = themed_embedding(theme_index=0, seed=2, noise=0.005)
    near_3 = themed_embedding(theme_index=0, seed=3, noise=0.005)

    a, b, c, d = (np.asarray(v) for v in (base, near, near_2, near_3))
    cos = float(a @ b)
    assert cos > 0.9  # sanity check on the fixture

    moments = _to_moments(
        [
            {"id": "v1", "title": "t", "narrative": "n", "embedding": base},
            {"id": "v2", "title": "t", "narrative": "n", "embedding": near},
            {"id": "v3", "title": "t", "narrative": "n", "embedding": near_2},
            {"id": "v4", "title": "t", "narrative": "n", "embedding": near_3},
        ]
    )

    clusters = run_hdbscan(moments, min_cluster_size=3)
    assert len(clusters) == 1
    assert set(clusters[0].member_moment_ids) == {"v1", "v2", "v3", "v4"}


def test_empty_input_returns_no_clusters():
    assert run_hdbscan([], min_cluster_size=3) == []
