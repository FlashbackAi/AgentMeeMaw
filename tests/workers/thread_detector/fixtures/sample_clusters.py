"""Deterministic embedding + moment fixtures for the Thread Detector tests.

The clustering tests exercise HDBSCAN's behaviour, so the embeddings
need to be tight enough on the same theme to form a cluster but far
enough from a different theme to split. We synthesize 1024-dim unit
vectors by combining a small per-theme "signal" vector with a low-
amplitude noise vector. The signal vectors are deliberately chosen
to be near-orthogonal, so two themes don't collapse into one cluster.
"""

from __future__ import annotations

import math
import random

import numpy as np


EMBEDDING_DIM = 1024


def _signal_vector(theme_index: int) -> np.ndarray:
    """One unit vector per theme. Different theme_index → orthogonal axes."""
    v = np.zeros(EMBEDDING_DIM, dtype=np.float64)
    # Spread the theme signal across a deterministic block to give HDBSCAN
    # plenty of room to separate themes.
    block = slice(theme_index * 8, theme_index * 8 + 8)
    v[block] = 1.0
    return v / math.sqrt(8)


def _noise_vector(seed: int) -> np.ndarray:
    rng = random.Random(seed)
    raw = np.array(
        [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)], dtype=np.float64
    )
    n = np.linalg.norm(raw)
    return raw if n == 0 else raw / n


def themed_embedding(theme_index: int, seed: int, *, noise: float = 0.01) -> list[float]:
    """A unit vector that lies near a theme's axis but not exactly on it.

    The default noise level is small enough that HDBSCAN will keep
    same-theme points inside the same cluster without flagging any of
    them as outliers — important for the deterministic clustering tests.
    """
    signal = _signal_vector(theme_index)
    noisy = signal + noise * _noise_vector(seed)
    norm = np.linalg.norm(noisy)
    if norm == 0:
        return signal.tolist()
    return (noisy / norm).tolist()


def make_themed_moments(
    theme_index: int, n: int, *, seed_offset: int = 0, noise: float = 0.01
) -> list[dict]:
    """Build N moment dicts for the same theme.

    Returned shape is ``[{"id": str, "title": str, "narrative": str,
    "embedding": list[float]}]``. Tests can lift these into
    :class:`ClusterableMoment` directly.
    """
    out: list[dict] = []
    for i in range(n):
        out.append(
            {
                "id": f"theme{theme_index}-m{i}",
                "title": f"Theme {theme_index} memory #{i}",
                "narrative": f"narrative for theme {theme_index} #{i}",
                "embedding": themed_embedding(
                    theme_index, seed=seed_offset + i, noise=noise
                ),
            }
        )
    return out
