"""HDBSCAN-based clustering of moment narrative embeddings.

The hdbscan library does not natively expose a cosine metric. The
standard workaround — and the one used here — is to L2-normalize the
input vectors and use Euclidean distance. On unit vectors,

    ||a - b||² = 2 - 2·cos(a, b)

so Euclidean distance is monotonic with cosine distance. The cluster
boundaries that fall out of HDBSCAN are therefore the same ones cosine
distance would produce.

Outliers (HDBSCAN label ``-1``) are explicitly DROPPED, not
force-clustered. A run with no clusters returns an empty list and the
worker exits cleanly with no thread writes.
"""

from __future__ import annotations

import numpy as np
import hdbscan

from .schema import Cluster, ClusterableMoment


def run_hdbscan(
    moments: list[ClusterableMoment],
    *,
    min_cluster_size: int,
) -> list[Cluster]:
    """Cluster moment embeddings into 0..N narrative threads.

    Parameters
    ----------
    moments
        Active moments with non-NULL narrative embeddings, all on the
        same embedding model identity. The caller scopes the query.
    min_cluster_size
        HDBSCAN's ``min_cluster_size``. Clusters smaller than this
        are not formed (their points become outliers).

    Returns
    -------
    list[Cluster]
        Zero or more clusters. Outliers are dropped.
    """
    if not moments or len(moments) < min_cluster_size:
        # Below min_cluster_size, no cluster can ever form. Skip even
        # invoking HDBSCAN — its `allow_single_cluster=True` flag will
        # otherwise produce a cluster of size 2 in pathological cases.
        return []

    embeddings = np.asarray([m.embedding for m in moments], dtype=np.float64)
    normalized = _l2_normalize(embeddings)

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="euclidean",
        # Per HDBSCAN docs, `allow_single_cluster` lets a homogeneous
        # run (one strong narrative arc spanning all moments) form a
        # cluster instead of being labeled as noise. Without it,
        # HDBSCAN refuses to form a single cluster because there is
        # no density contrast to anchor it against.
        allow_single_cluster=True,
    )
    labels = clusterer.fit_predict(normalized)
    probabilities = getattr(clusterer, "probabilities_", None)
    if probabilities is None:
        probabilities = np.ones(len(moments), dtype=np.float64)

    clusters_by_label: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        label_int = int(label)
        if label_int == -1:
            continue
        clusters_by_label.setdefault(label_int, []).append(idx)

    out: list[Cluster] = []
    for indexes in clusters_by_label.values():
        member_embeddings = embeddings[indexes]
        centroid = member_embeddings.mean(axis=0)
        centroid_norm = float(np.linalg.norm(centroid))
        if centroid_norm > 0:
            centroid = centroid / centroid_norm
        member_probs = probabilities[indexes]
        confidence = float(np.clip(member_probs.mean(), 0.0, 1.0))
        out.append(
            Cluster(
                member_moment_ids=[moments[i].id for i in indexes],
                member_embeddings=member_embeddings,
                centroid=centroid,
                confidence=confidence,
            )
        )
    return out


def count_outliers(moments: list[ClusterableMoment], clusters: list[Cluster]) -> int:
    """How many of the input moments are NOT assigned to any cluster."""
    in_cluster = {mid for c in clusters for mid in c.member_moment_ids}
    return sum(1 for m in moments if m.id not in in_cluster)


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalize. Zero rows pass through unchanged."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe = np.where(norms > 0, norms, 1.0)
    return matrix / safe
