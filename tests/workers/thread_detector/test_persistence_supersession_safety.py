"""Supersession-safety: superseded moments must NOT enter clustering.

Per CLAUDE.md §4 invariant #1, every read against canonical tables
filters ``status='active'``. The Thread Detector pulls clusterable
moments via the ``active_moments`` view, so a moment that was
superseded between extraction and a Thread Detector run is invisible
to this run — even if it carried an embedding when it was active.
"""

from __future__ import annotations

from flashback.workers.thread_detector.persistence import (
    fetch_clusterable_moments,
)
from tests.workers.thread_detector.fixtures.sample_clusters import (
    themed_embedding,
)

MODEL = "voyage-3-large"
VERSION = "2025-01-07"


def _seed_moment(
    db_pool,
    *,
    person_id: str,
    title: str,
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
                VALUES (%s, %s, 'narr', %s, %s::vector, %s, %s)
                RETURNING id::text
                """,
                (person_id, title, status, embedding, MODEL, VERSION),
            )
            mid = cur.fetchone()[0]
            conn.commit()
    return mid


def test_superseded_moment_filtered_out(db_pool, make_person):
    person_id = make_person("Sup A")

    embedding = themed_embedding(theme_index=0, seed=1, noise=0.02)
    active_id = _seed_moment(
        db_pool,
        person_id=person_id,
        title="kept active",
        embedding=embedding,
    )

    superseded_id = _seed_moment(
        db_pool,
        person_id=person_id,
        title="now superseded",
        embedding=embedding,
        status="superseded",
    )

    rows = fetch_clusterable_moments(
        db_pool,
        person_id=person_id,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )

    ids = {r.id for r in rows}
    assert active_id in ids
    assert superseded_id not in ids
