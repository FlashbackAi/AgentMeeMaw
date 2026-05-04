"""Match a cluster centroid to an existing thread, or signal create-new.

Per ARCHITECTURE.md §3.13 the Thread Detector is a match-or-create
process. We look up the closest active thread by cosine distance to
the cluster centroid (under the same person_id and same embedding model
identity, per invariants #1, #2, #3). If the distance is below a
configurable threshold (default 0.4) we link to that thread. Otherwise
we name a new one.
"""

from __future__ import annotations

import structlog

from .schema import Cluster, ThreadMatchResult, ThreadSnapshot

log = structlog.get_logger("flashback.workers.thread_detector.matching")


def match_existing_thread(
    db_pool,
    *,
    cluster: Cluster,
    person_id: str,
    distance_threshold: float,
    embedding_model: str,
    embedding_model_version: str,
) -> ThreadMatchResult:
    """Return the closest active thread within ``distance_threshold``.

    If no active threads exist, both fields of the returned result are
    ``None``. If a candidate exists but is too far, only
    ``existing_thread_distance`` is set; the worker treats this as
    "create new".
    """
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text,
                       (description_embedding <=> %(centroid)s::vector) AS distance
                  FROM active_threads
                 WHERE person_id              = %(person_id)s
                   AND embedding_model         = %(model)s
                   AND embedding_model_version = %(version)s
                   AND description_embedding IS NOT NULL
                 ORDER BY description_embedding <=> %(centroid)s::vector
                 LIMIT 1
                """,
                {
                    "centroid": cluster.centroid.tolist(),
                    "person_id": person_id,
                    "model": embedding_model,
                    "version": embedding_model_version,
                },
            )
            row = cur.fetchone()

    if row is None:
        return ThreadMatchResult(
            existing_thread_id=None, existing_thread_distance=None
        )

    thread_id, distance = row[0], float(row[1])
    if distance < distance_threshold:
        log.info(
            "thread_detector.matched_existing",
            thread_id=thread_id,
            distance=distance,
            threshold=distance_threshold,
        )
        return ThreadMatchResult(
            existing_thread_id=thread_id, existing_thread_distance=distance
        )

    log.info(
        "thread_detector.no_match_within_threshold",
        closest_thread_id=thread_id,
        closest_distance=distance,
        threshold=distance_threshold,
    )
    return ThreadMatchResult(
        existing_thread_id=None, existing_thread_distance=distance
    )


def fetch_thread_snapshot(db_pool, *, thread_id: str) -> ThreadSnapshot:
    """Read name + description for an existing thread (for the P4 prompt)."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, name, description
                  FROM active_threads
                 WHERE id = %s
                """,
                (thread_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise ValueError(f"thread {thread_id!r} not found")
    return ThreadSnapshot(id=row[0], name=row[1], description=row[2])
