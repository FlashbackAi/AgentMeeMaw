"""Per-cluster persistence for the Thread Detector.

The unit of work is one cluster. For each cluster:

  1. **Match-or-create** (read-only DB) — find the closest active thread.
  2. **Naming LLM** (only for new threads, OUTSIDE any DB transaction).
     If ``coherent`` is false, the cluster is dropped.
  3. **Single transaction**:
       a. Insert thread row (new path) OR no-op (existing path).
       b. Insert ``evidences`` edges from each member moment to the
          thread, ``ON CONFLICT DO NOTHING`` so that retries don't fail
          on duplicates.
  4. **P4 LLM** (OUTSIDE any DB transaction). Generates 1–2
     ``thread_deepen`` questions for the thread.
  5. **Single transaction** — insert the question rows + their
     ``motivated_by`` edges back to the thread.
  6. **Post-commit** — push thread embedding + artifact jobs (new path
     only) and per-question embedding jobs.

LLM calls are deliberately kept OUT of DB transactions so we never hold
a Postgres connection open across a Sonnet round-trip. The two-stage
transactional split is safe because of the trigger-baseline mechanic:
``persons.moments_at_last_thread_run`` is only updated at the END of a
run, so any failure between writes leaves the trigger sticky and the
worker re-runs against the same baseline. Re-runs collapse to no-ops
via the existing-thread match path (the freshly-written thread is now
the closest match) and ``ON CONFLICT DO NOTHING`` on evidences edges.

Invariants honoured (CLAUDE.md §4):

* #1 — every read scopes to ``status='active'`` via the
  ``active_*`` views (cluster fetch, thread match).
* #2 — every write carries the legacy ``person_id``.
* #3 — embeddings only ever stamped with the configured
  ``embedding_model`` / ``embedding_model_version``.
* #4 — vectors never written here. Embedding jobs go to SQS post-commit.
* #5 — supersession is read-only here (we filter on ``active_moments``);
  any moment that was superseded between extraction and detection is
  invisible to the cluster.
* #9 — every produced question carries ``themes`` via :class:`P4Result`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from psycopg.types.json import Json

from flashback.db.edges import validate_edge

from .matching import fetch_thread_snapshot, match_existing_thread
from .naming_llm import NamingLLMConfig, name_cluster
from .p4_llm import P4LLMConfig, propose_thread_deepen_questions
from .schema import (
    Cluster,
    ClusterableMoment,
    NamingResult,
    P4Result,
    ThreadMatchResult,
    ThreadSnapshot,
)

log = structlog.get_logger("flashback.workers.thread_detector.persistence")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ClusterOutcome:
    """What happened with one cluster on this run."""

    cluster_member_count: int
    matched_existing: bool = False
    incoherent: bool = False
    thread_id: str | None = None
    thread_was_created: bool = False
    new_evidences_edge_count: int = 0
    questions_inserted: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point — process one cluster
# ---------------------------------------------------------------------------


def process_cluster(
    *,
    db_pool,
    cluster: Cluster,
    member_moments: list[ClusterableMoment],
    person_id: str,
    person_name: str,
    naming_cfg: NamingLLMConfig,
    p4_cfg: P4LLMConfig,
    settings,
    embedding_model: str,
    embedding_model_version: str,
    distance_threshold: float,
    embedding_job_pusher,
    artifact_job_pusher,
) -> ClusterOutcome:
    """Persist one cluster end-to-end. Returns a :class:`ClusterOutcome`.

    ``embedding_job_pusher`` and ``artifact_job_pusher`` are callables
    so the worker can swap real SQS senders for stubs in tests without
    threading a SQS-shaped object through every layer.

    Failures from the LLM or the database are allowed to propagate: the
    worker treats one cluster's failure as recoverable and continues
    with the next cluster.
    """
    outcome = ClusterOutcome(cluster_member_count=len(cluster.member_moment_ids))

    # 1. Match-or-create read.
    match = match_existing_thread(
        db_pool,
        cluster=cluster,
        person_id=person_id,
        distance_threshold=distance_threshold,
        embedding_model=embedding_model,
        embedding_model_version=embedding_model_version,
    )

    naming: NamingResult | None = None
    if not match.is_match:
        # 2. Naming LLM (outside DB transaction).
        naming = name_cluster(
            cfg=naming_cfg,
            settings=settings,
            person_name=person_name,
            member_moments=member_moments,
        )
        if not naming.coherent:
            outcome.incoherent = True
            log.info(
                "thread_detector.cluster_not_coherent",
                reasoning=naming.reasoning,
                cluster_size=len(cluster.member_moment_ids),
            )
            return outcome
        if not (naming.name and naming.description and naming.generation_prompt):
            log.warning(
                "thread_detector.naming_incomplete",
                name=naming.name,
                description_set=bool(naming.description),
                gen_prompt_set=bool(naming.generation_prompt),
            )
            return outcome

    # 3. Transaction A — thread + evidences.
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                if match.is_match:
                    thread_id = match.existing_thread_id  # type: ignore[assignment]
                    outcome.matched_existing = True
                    outcome.thread_id = thread_id
                    log.info(
                        "thread_detector.linking_to_existing_thread",
                        thread_id=thread_id,
                        distance=match.existing_thread_distance,
                    )
                else:
                    assert naming is not None  # for type narrowing
                    thread_id = _insert_thread(
                        cur,
                        person_id=person_id,
                        naming=naming,
                        confidence=cluster.confidence,
                    )
                    outcome.thread_id = thread_id
                    outcome.thread_was_created = True

                outcome.new_evidences_edge_count = _insert_evidences_edges(
                    cur,
                    moment_ids=cluster.member_moment_ids,
                    thread_id=thread_id,
                )

    # 4. P4 LLM (outside DB transaction).
    if naming is not None:
        thread_snapshot = ThreadSnapshot.from_naming(
            thread_id=outcome.thread_id, naming=naming  # type: ignore[arg-type]
        )
    else:
        thread_snapshot = fetch_thread_snapshot(
            db_pool, thread_id=outcome.thread_id  # type: ignore[arg-type]
        )

    p4_result = propose_thread_deepen_questions(
        cfg=p4_cfg,
        settings=settings,
        person_name=person_name,
        thread=thread_snapshot,
        member_moments=member_moments,
    )

    # 5. Transaction B — questions + motivated_by edges.
    question_ids: list[str] = []
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for q in p4_result.questions:
                    qid = _insert_thread_deepen_question(
                        cur,
                        person_id=person_id,
                        text=q.text,
                        themes=list(q.themes),
                    )
                    question_ids.append(qid)
                    _insert_validated_edge(
                        cur,
                        from_kind="question",
                        from_id=qid,
                        to_kind="thread",
                        to_id=thread_snapshot.id,
                        edge_type="motivated_by",
                    )
    outcome.questions_inserted = question_ids

    # 6. Post-commit — embedding + artifact pushes.
    if outcome.thread_was_created and naming is not None:
        embedding_job_pusher(
            record_type="thread",
            record_id=thread_snapshot.id,
            source_text=f"{naming.name}, {naming.description}",
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )
        if naming.generation_prompt:
            artifact_job_pusher(
                record_type="thread",
                record_id=thread_snapshot.id,
                person_id=person_id,
                artifact_kind="image",
                generation_prompt=naming.generation_prompt,
            )

    for qid, q in zip(question_ids, p4_result.questions, strict=True):
        embedding_job_pusher(
            record_type="question",
            record_id=qid,
            source_text=q.text,
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )

    return outcome


# ---------------------------------------------------------------------------
# Inserts
# ---------------------------------------------------------------------------


def _insert_thread(
    cur,
    *,
    person_id: str,
    naming: NamingResult,
    confidence: float,
) -> str:
    cur.execute(
        """
        INSERT INTO threads
              (person_id, name, description, source, confidence,
               generation_prompt)
        VALUES (%s,        %s,   %s,          'auto-detected', %s,
                %s)
        RETURNING id::text
        """,
        (
            person_id,
            naming.name,
            naming.description,
            confidence,
            naming.generation_prompt,
        ),
    )
    return cur.fetchone()[0]


def _insert_evidences_edges(
    cur,
    *,
    moment_ids: list[str],
    thread_id: str,
) -> int:
    """Insert one ``evidences`` edge per member moment.

    Uses ``ON CONFLICT DO NOTHING`` on the unique
    ``(from_kind, from_id, to_kind, to_id, edge_type)`` constraint so
    re-runs of a cluster do not raise.
    """
    inserted = 0
    for mid in moment_ids:
        validate_edge("moment", "thread", "evidences")
        cur.execute(
            """
            INSERT INTO edges (from_kind, from_id, to_kind, to_id,
                               edge_type, attributes)
            VALUES ('moment', %s, 'thread', %s, 'evidences', %s)
            ON CONFLICT (from_kind, from_id, to_kind, to_id, edge_type)
            DO NOTHING
            RETURNING id
            """,
            (mid, thread_id, Json({})),
        )
        if cur.fetchone() is not None:
            inserted += 1
    return inserted


def _insert_thread_deepen_question(
    cur,
    *,
    person_id: str,
    text: str,
    themes: list[str],
) -> str:
    cur.execute(
        """
        INSERT INTO questions
              (person_id, text, source, attributes)
        VALUES (%s,        %s,   'thread_deepen', %s)
        RETURNING id::text
        """,
        (person_id, text, Json({"themes": themes})),
    )
    return cur.fetchone()[0]


def _insert_validated_edge(
    cur,
    *,
    from_kind: str,
    from_id: str,
    to_kind: str,
    to_id: str,
    edge_type: str,
) -> None:
    validate_edge(from_kind, to_kind, edge_type)
    cur.execute(
        """
        INSERT INTO edges (from_kind, from_id, to_kind, to_id,
                           edge_type, attributes)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (from_kind, from_id, to_kind, to_id, edge_type)
        DO NOTHING
        """,
        (from_kind, from_id, to_kind, to_id, edge_type, Json({})),
    )


# ---------------------------------------------------------------------------
# Cluster moment fetch
# ---------------------------------------------------------------------------


def fetch_clusterable_moments(
    db_pool,
    *,
    person_id: str,
    embedding_model: str,
    embedding_model_version: str,
) -> list[ClusterableMoment]:
    """Pull active moments with the configured embedding model.

    NULL-embedding moments are skipped — they'll be picked up on the
    next trigger after the embedding worker has filled them in.
    Moments still on a stale embedding model are also skipped (per
    invariant #3).
    """
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, title, narrative, narrative_embedding
                  FROM active_moments
                 WHERE person_id              = %(pid)s
                   AND embedding_model         = %(model)s
                   AND embedding_model_version = %(version)s
                   AND narrative_embedding IS NOT NULL
                """,
                {
                    "pid": person_id,
                    "model": embedding_model,
                    "version": embedding_model_version,
                },
            )
            rows = cur.fetchall()

    out: list[ClusterableMoment] = []
    for row in rows:
        moment_id, title, narrative, embedding = row
        out.append(
            ClusterableMoment(
                id=moment_id,
                title=title,
                narrative=narrative,
                embedding=list(embedding),
            )
        )
    return out


def fetch_person_name(db_pool, *, person_id: str) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM persons WHERE id = %s",
                (person_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise ValueError(f"person {person_id!r} not found")
    return str(row[0])
