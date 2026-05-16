"""Per-cluster persistence for the Thread Detector.

The unit of work is one cluster. For each cluster:

  1. **Match-or-create** (read-only DB) — find the closest active thread.
  2. **Naming LLM** (only for new threads, OUTSIDE any DB transaction).
     If ``coherent`` is false, the cluster is dropped.
  3. **P4 LLM** (OUTSIDE any DB transaction). Generates 1–2
     ``thread_deepen`` questions for the thread.
  4. **Single transaction**:
       a. Insert thread row (new path) OR no-op (existing path).
       b. Insert ``evidences`` edges from each member moment to the
          thread, ``ON CONFLICT DO NOTHING`` so that retries don't fail
          on duplicates.
       c. Insert the question rows + their ``motivated_by`` edges back
          to the thread.
  5. **Post-commit** — push thread embedding + artifact jobs (new path
     only) and per-question embedding jobs.

LLM calls are deliberately kept OUT of DB transactions so we never hold
a Postgres connection open across a Sonnet round-trip. Once the naming
and P4 outputs are in hand, all durable writes for the cluster land in a
single transaction so we do not create a thread without its initial P4
questions.

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
from uuid import uuid4

import structlog
from psycopg.types.json import Json

from flashback.db.edges import validate_edge
from flashback.themes.archetype_llm import (
    ArchetypeContextMoment,
    ArchetypeQuestion,
    generate_archetype_questions_sync,
)
from flashback.themes.repository import insert_emergent_theme_sync
from flashback.themes.universal import UNIVERSAL_THEME_SLUGS

from .matching import fetch_thread_snapshot, match_existing_thread
from .naming_llm import (
    THREAD_NAMING_PROMPT_VERSION,
    NamingLLMConfig,
    name_cluster,
)
from .p4_llm import P4_PROMPT_VERSION, P4LLMConfig, propose_thread_deepen_questions
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

    emergent_theme_id: str | None = None
    """Set when this cluster promoted to a brand-new emergent theme."""
    themed_as_edge_count: int = 0
    """Number of themed_as edges (moment -> theme) written for this cluster."""


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
    contributor_display_name: str = "",
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
            contributor_display_name=contributor_display_name,
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

    # 3. Resolve the thread snapshot before any write. For new threads,
    # preallocate the UUID so P4 can reference the same id that will be
    # inserted in the transaction below.
    if naming is not None:
        thread_id = str(uuid4())
        thread_snapshot = ThreadSnapshot.from_naming(
            thread_id=thread_id,
            naming=naming,
        )
    else:
        thread_id = match.existing_thread_id  # type: ignore[assignment]
        outcome.matched_existing = True
        log.info(
            "thread_detector.linking_to_existing_thread",
            thread_id=thread_id,
            distance=match.existing_thread_distance,
        )
        thread_snapshot = fetch_thread_snapshot(
            db_pool,
            thread_id=thread_id,
        )
    outcome.thread_id = thread_id
    outcome.thread_was_created = naming is not None

    # 4. P4 LLM runs before DB writes so all DB mutations below commit together.
    p4_result = propose_thread_deepen_questions(
        cfg=p4_cfg,
        settings=settings,
        person_name=person_name,
        thread=thread_snapshot,
        member_moments=member_moments,
        contributor_display_name=contributor_display_name,
    )

    # 4b. Emergent-theme archetype generation (only on the new-thread path
    # AND when the naming LLM indicated this cluster is a new emergent
    # theme, not just another instance of a universal). Eager generation
    # here so the user-facing unlock tap is snappy later.
    archetype_questions: list[ArchetypeQuestion] = []
    emergent_slug: str | None = None
    if (
        naming is not None
        and naming.has_emergent_theme()
        and naming.theme_slug not in UNIVERSAL_THEME_SLUGS  # extra guardrail
    ):
        emergent_slug = naming.theme_slug
        archetype_questions = generate_archetype_questions_sync(
            settings=settings,
            theme_slug=naming.theme_slug or "",
            theme_display_name=naming.theme_display_name or "",
            theme_description=naming.theme_description or "",
            theme_kind="emergent",
            subject_name=person_name,
            subject_relationship=None,
            context_moments=[
                ArchetypeContextMoment(title=m.title, narrative=m.narrative)
                for m in member_moments
            ],
        )

    # 5. Single transaction — thread/theme/evidences/questions/edges.
    question_ids: list[str] = []
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                if naming is not None:
                    _insert_thread(
                        cur,
                        thread_id=thread_id,
                        person_id=person_id,
                        naming=naming,
                        confidence=cluster.confidence,
                        llm_provider=naming_cfg.provider,
                        llm_model=naming_cfg.model,
                        prompt_version=THREAD_NAMING_PROMPT_VERSION,
                    )

                # Decide which (if any) emergent theme to back-tag cluster
                # moments to. New-thread + emergent? Insert + tag. Existing
                # match? Look up the existing thread's emergent theme.
                target_theme_id: str | None = None
                if naming is not None and emergent_slug is not None:
                    new_theme_id = insert_emergent_theme_sync(
                        cur,
                        person_id=person_id,
                        slug=emergent_slug,
                        display_name=naming.theme_display_name or "",
                        description=naming.theme_description,
                        thread_id=thread_id,
                        archetype_questions=(
                            [q.to_payload() for q in archetype_questions]
                            if archetype_questions
                            else None
                        ),
                        generation_prompt=naming.generation_prompt,
                    )
                    if new_theme_id is not None:
                        outcome.emergent_theme_id = new_theme_id
                        target_theme_id = new_theme_id
                    else:
                        # Conflict: another path already created an active
                        # theme with this slug for this person. Look it up
                        # and use it as the back-tag target.
                        target_theme_id = _find_active_theme_id_by_slug(
                            cur, person_id=person_id, slug=emergent_slug
                        )
                elif outcome.matched_existing:
                    target_theme_id = _find_active_theme_id_by_thread(
                        cur, person_id=person_id, thread_id=thread_id
                    )

                if target_theme_id is not None:
                    outcome.themed_as_edge_count = _insert_themed_as_edges(
                        cur,
                        moment_ids=cluster.member_moment_ids,
                        theme_id=target_theme_id,
                    )

                outcome.new_evidences_edge_count = _insert_evidences_edges(
                    cur,
                    moment_ids=cluster.member_moment_ids,
                    thread_id=thread_id,
                )
                for q in p4_result.questions:
                    qid = _insert_thread_deepen_question(
                        cur,
                        person_id=person_id,
                        text=q.text,
                        themes=list(q.themes),
                        llm_provider=p4_cfg.provider,
                        llm_model=p4_cfg.model,
                        prompt_version=P4_PROMPT_VERSION,
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

    # 5. Post-commit — embedding + artifact pushes.
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
    thread_id: str,
    person_id: str,
    naming: NamingResult,
    confidence: float,
    llm_provider: str,
    llm_model: str,
    prompt_version: str,
) -> None:
    cur.execute(
        """
        INSERT INTO threads
              (id, person_id, name, description, source, confidence,
               generation_prompt, llm_provider, llm_model, prompt_version)
        VALUES (%s, %s,        %s,   %s,          'auto-detected', %s,
                %s,                %s,           %s,        %s)
        """,
        (
            thread_id,
            person_id,
            naming.name,
            naming.description,
            confidence,
            naming.generation_prompt,
            llm_provider,
            llm_model,
            prompt_version,
        ),
    )


def _insert_themed_as_edges(
    cur,
    *,
    moment_ids: list[str],
    theme_id: str,
) -> int:
    """Insert one ``themed_as`` edge per cluster member moment to a theme.

    Uses ``ON CONFLICT DO NOTHING`` on the unique edge constraint so
    re-runs (and previous extraction-time tags by the LLM) don't
    collide.
    """
    inserted = 0
    for mid in moment_ids:
        validate_edge("moment", "theme", "themed_as")
        cur.execute(
            """
            INSERT INTO edges (from_kind, from_id, to_kind, to_id,
                               edge_type, attributes)
            VALUES ('moment', %s, 'theme', %s, 'themed_as', %s)
            ON CONFLICT (from_kind, from_id, to_kind, to_id, edge_type)
            DO NOTHING
            RETURNING id
            """,
            (mid, theme_id, Json({})),
        )
        if cur.fetchone() is not None:
            inserted += 1
    return inserted


def _find_active_theme_id_by_slug(
    cur, *, person_id: str, slug: str
) -> str | None:
    cur.execute(
        """
        SELECT id::text FROM active_themes
         WHERE person_id = %s AND slug = %s
         LIMIT 1
        """,
        (person_id, slug),
    )
    row = cur.fetchone()
    return row[0] if row is not None else None


def _find_active_theme_id_by_thread(
    cur, *, person_id: str, thread_id: str
) -> str | None:
    """Find the active emergent theme (if any) that backs a given thread."""
    cur.execute(
        """
        SELECT id::text FROM active_themes
         WHERE person_id = %s
           AND kind = 'emergent'
           AND thread_id = %s
         LIMIT 1
        """,
        (person_id, thread_id),
    )
    row = cur.fetchone()
    return row[0] if row is not None else None


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
    llm_provider: str,
    llm_model: str,
    prompt_version: str,
) -> str:
    cur.execute(
        """
        INSERT INTO questions
              (person_id, text, source, attributes,
               llm_provider, llm_model, prompt_version)
        VALUES (%s,        %s,   'thread_deepen', %s,
                %s,           %s,        %s)
        RETURNING id::text
        """,
        (
            person_id,
            text,
            Json({"themes": themes}),
            llm_provider,
            llm_model,
            prompt_version,
        ),
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
