"""
End-to-end worker test (DB-touching).

Mocks LLMs (extraction + compatibility) and the SQS surface; exercises
the real persistence transaction, post-commit pushes, and ack behaviour.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from flashback.llm.errors import LLMError
from flashback.workers.extraction import (
    extraction_llm as ext_llm_mod,
    compatibility_llm as compat_mod,
    trait_merge_llm as trait_merge_mod,
)
from flashback.workers.extraction.worker import ExtractionWorker
from tests.workers.extraction.conftest import (
    StubExtractionSQSClient,
    StubSQSArtifactSender,
    StubSQSEmbeddingSender,
    StubSQSThreadDetectorSender,
    StubVoyage,
    make_received_message,
)
from tests.workers.extraction.fixtures import sample_extractions


def _stub_call(returns_or_seq, exc: Exception | None = None):
    """Async stub for ``call_with_tool``. Accepts a single dict or a list."""
    if isinstance(returns_or_seq, list):
        seq = list(returns_or_seq)

        async def _impl(**kwargs):
            return seq.pop(0)

        return _impl

    async def _impl(**kwargs):
        if exc is not None:
            raise exc
        return returns_or_seq

    return _impl


def _build_worker(
    *,
    db_pool,
    extraction_cfg,
    compat_cfg,
    trait_merge_cfg,
    settings,
    voyage=None,
    sqs=None,
) -> ExtractionWorker:
    return ExtractionWorker(
        db_pool=db_pool,
        sqs=sqs or StubExtractionSQSClient(),
        embedding_sender=StubSQSEmbeddingSender(),
        artifact_sender=StubSQSArtifactSender(),
        thread_detector_sender=StubSQSThreadDetectorSender(),
        voyage=voyage or StubVoyage(return_none=True),
        extraction_cfg=extraction_cfg,
        compatibility_cfg=compat_cfg,
        trait_merge_cfg=trait_merge_cfg,
        settings=settings,
        embedding_model="voyage-3-large",
        embedding_model_version="2025-01-07",
    )


def test_happy_path_writes_graph_and_pushes_jobs(
    db_pool,
    make_person,
    monkeypatch,
    stub_extraction_cfg,
    stub_compat_cfg,
    stub_trait_merge_cfg,
    stub_settings,
):
    person_id = make_person("Dad Smith")

    payload = sample_extractions.clean_extraction()
    monkeypatch.setattr(
        ext_llm_mod, "call_with_tool", _stub_call(payload)
    )

    worker = _build_worker(
        db_pool=db_pool,
        extraction_cfg=stub_extraction_cfg,
        compat_cfg=stub_compat_cfg,
        trait_merge_cfg=stub_trait_merge_cfg,
        settings=stub_settings,
    )
    msg = make_received_message(person_id=person_id)
    worker.process_message(msg)

    # SQS message acked.
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]

    # DB has expected rows.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM moments WHERE person_id=%s", (person_id,)
            )
            assert cur.fetchone()[0] == 2
            cur.execute(
                "SELECT count(*) FROM entities WHERE person_id=%s", (person_id,)
            )
            assert cur.fetchone()[0] == 3
            cur.execute(
                "SELECT count(*) FROM traits WHERE person_id=%s", (person_id,)
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                """SELECT count(*) FROM questions
                   WHERE person_id=%s AND source='dropped_reference'""",
                (person_id,),
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                "SELECT phase, coverage_state FROM persons WHERE id=%s",
                (person_id,),
            )
            phase, coverage = cur.fetchone()
            # Coverage incremented for the moments.
            assert int(coverage["sensory"]) >= 1
            assert int(coverage["place"]) >= 1
            # Subject's name in fixture ("Dad") doesn't match make_person's
            # name, so phase may flip — we don't assert specifically here.
            assert phase in ("starter", "steady")

    # Embedding pushes: 2 moments + 3 entities (with description) + 1 trait
    # + 1 dropped-reference question = 7.
    embedding_sender = worker.embedding_sender  # type: ignore[attr-defined]
    assert len(embedding_sender.sent) == 7
    record_types = sorted(p["record_type"] for p in embedding_sender.sent)
    assert record_types == [
        "entity",
        "entity",
        "entity",
        "moment",
        "moment",
        "question",
        "trait",
    ]

    # Artifact pushes: 2 moments (videos) + 3 entities (images) = 5.
    artifact_sender = worker.artifact_sender  # type: ignore[attr-defined]
    assert len(artifact_sender.sent) == 5
    artifact_kinds = sorted(p["artifact_kind"] for p in artifact_sender.sent)
    assert artifact_kinds == ["image", "image", "image", "video", "video"]

    # Idempotency row written.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT moments_written FROM processed_extractions WHERE sqs_message_id=%s",
                (msg.message_id,),
            )
            (n,) = cur.fetchone()
    assert n == 2


def test_idempotent_redelivery_acks_and_skips(
    db_pool,
    make_person,
    monkeypatch,
    stub_extraction_cfg,
    stub_compat_cfg,
    stub_trait_merge_cfg,
    stub_settings,
):
    person_id = make_person("Idem D")
    payload = sample_extractions.empty_extraction()
    monkeypatch.setattr(
        ext_llm_mod, "call_with_tool", _stub_call(payload)
    )

    worker = _build_worker(
        db_pool=db_pool,
        extraction_cfg=stub_extraction_cfg,
        compat_cfg=stub_compat_cfg,
        trait_merge_cfg=stub_trait_merge_cfg,
        settings=stub_settings,
    )
    msg = make_received_message(person_id=person_id)

    worker.process_message(msg)
    assert worker.sqs.deleted == [msg.receipt_handle]  # type: ignore[attr-defined]

    # Second time around, same message_id: should ack immediately and not
    # call the LLM again.
    monkeypatch.setattr(
        ext_llm_mod,
        "call_with_tool",
        _stub_call({}, exc=AssertionError("LLM should not be called")),
    )
    worker.process_message(msg)
    assert worker.sqs.deleted == [  # type: ignore[attr-defined]
        msg.receipt_handle,
        msg.receipt_handle,
    ]


def test_llm_failure_does_not_ack_or_persist(
    db_pool,
    make_person,
    monkeypatch,
    stub_extraction_cfg,
    stub_compat_cfg,
    stub_trait_merge_cfg,
    stub_settings,
):
    person_id = make_person("Fail A")
    monkeypatch.setattr(
        ext_llm_mod,
        "call_with_tool",
        _stub_call({}, exc=LLMError("boom")),
    )
    worker = _build_worker(
        db_pool=db_pool,
        extraction_cfg=stub_extraction_cfg,
        compat_cfg=stub_compat_cfg,
        trait_merge_cfg=stub_trait_merge_cfg,
        settings=stub_settings,
    )
    msg = make_received_message(person_id=person_id)
    worker.process_message(msg)

    # Not acked.
    assert worker.sqs.deleted == []  # type: ignore[attr-defined]

    # Nothing persisted.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM moments WHERE person_id=%s", (person_id,)
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT count(*) FROM processed_extractions WHERE sqs_message_id=%s",
                (msg.message_id,),
            )
            assert cur.fetchone()[0] == 0


def test_refinement_supersedes_existing_moment(
    db_pool,
    make_person,
    monkeypatch,
    stub_extraction_cfg,
    stub_compat_cfg,
    stub_trait_merge_cfg,
    stub_settings,
):
    person_id = make_person("Sup E")

    # Seed an existing active moment with embedding + an involves edge to
    # an entity that the new extraction will also reference.
    seed_vec = [0.5] * 1024
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments (person_id, title, narrative,
                                     narrative_embedding, embedding_model, embedding_model_version)
                VALUES (%s, 'old pancakes', 'older retelling about pancakes',
                        %s::vector, 'voyage-3-large', '2025-01-07')
                RETURNING id::text
                """,
                (person_id, seed_vec),
            )
            old_moment_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO entities (person_id, kind, name)
                VALUES (%s, 'place', 'Family kitchen')
                RETURNING id::text
                """,
                (person_id,),
            )
            old_entity_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO edges (from_kind, from_id, to_kind, to_id, edge_type)
                VALUES ('moment', %s, 'entity', %s, 'involves')
                """,
                (old_moment_id, old_entity_id),
            )
            conn.commit()

    # Extraction LLM returns the clean fixture (mentions "Family kitchen").
    monkeypatch.setattr(
        ext_llm_mod,
        "call_with_tool",
        _stub_call(sample_extractions.clean_extraction()),
    )
    # Compatibility LLM votes "refinement" for whatever it sees.
    monkeypatch.setattr(
        compat_mod,
        "call_with_tool",
        _stub_call(
            {"verdict": "refinement", "reasoning": "same memory"}
        ),
    )

    worker = _build_worker(
        db_pool=db_pool,
        extraction_cfg=stub_extraction_cfg,
        compat_cfg=stub_compat_cfg,
        trait_merge_cfg=stub_trait_merge_cfg,
        settings=stub_settings,
        voyage=StubVoyage(vector=seed_vec),  # close hit
    )
    msg = make_received_message(person_id=person_id)
    worker.process_message(msg)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, superseded_by FROM moments WHERE id=%s",
                (old_moment_id,),
            )
            status, superseded_by = cur.fetchone()
    assert status == "superseded"
    assert superseded_by is not None


def test_existing_trait_merges_instead_of_inserting_duplicate(
    db_pool,
    make_person,
    monkeypatch,
    stub_extraction_cfg,
    stub_compat_cfg,
    stub_trait_merge_cfg,
    stub_settings,
):
    """Invariant #18 cross-session merge: a re-encountered trait name UPDATEs
    the existing row with the LLM-merged description (no duplicate insert),
    clears embedding fields, and pushes a single embedding job that re-embeds
    the merged description against the existing trait id."""
    person_id = make_person("Cross Session Subject")
    seed_vec = [0.5] * 1024

    # Seed an existing active trait named "warmth" with an embedding (so we
    # can prove the worker NULLed the embedding fields on merge).
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO traits
                      (person_id, name, description, strength,
                       description_embedding, embedding_model, embedding_model_version)
                VALUES (%s, 'warmth',
                        'Made strangers feel at home over coffee.',
                        'mentioned_once',
                        %s::vector, 'voyage-3-large', '2025-01-07')
                RETURNING id::text
                """,
                (person_id, seed_vec),
            )
            existing_trait_id = cur.fetchone()[0]
            conn.commit()

    # Extraction LLM emits the clean fixture, which contains a trait named
    # "warmth" with description "Welcoming and generous." linked from moment 0.
    monkeypatch.setattr(
        ext_llm_mod, "call_with_tool", _stub_call(sample_extractions.clean_extraction())
    )
    # Trait-merge LLM returns a blended description.
    merged_description = (
        "Welcoming and generous — made strangers feel at home over coffee "
        "and over Sunday-morning pancakes."
    )
    monkeypatch.setattr(
        trait_merge_mod,
        "call_with_tool",
        _stub_call({"merged_description": merged_description}),
    )

    worker = _build_worker(
        db_pool=db_pool,
        extraction_cfg=stub_extraction_cfg,
        compat_cfg=stub_compat_cfg,
        trait_merge_cfg=stub_trait_merge_cfg,
        settings=stub_settings,
    )
    msg = make_received_message(person_id=person_id)
    worker.process_message(msg)

    # Exactly one active "warmth" row — no duplicate insert — and it has the
    # merged description with NULLed embedding fields.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, description, description_embedding,
                       embedding_model, embedding_model_version
                  FROM active_traits
                 WHERE person_id=%s AND lower(name)='warmth'
                """,
                (person_id,),
            )
            rows = cur.fetchall()
    assert len(rows) == 1, "duplicate warmth trait should not have been inserted"
    row_id, description, embedding, emb_model, emb_version = rows[0]
    assert row_id == existing_trait_id  # UPDATED in place
    assert description == merged_description
    assert embedding is None
    assert emb_model is None
    assert emb_version is None

    # The exemplifies edge from moment 0 of the fixture points at the
    # existing trait id (not a new one).
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                  FROM edges
                 WHERE edge_type='exemplifies'
                   AND to_kind='trait'
                   AND to_id=%s
                """,
                (existing_trait_id,),
            )
            assert cur.fetchone()[0] >= 1

    # Embedding job pushed for the merged trait (re-embed), targeting the
    # existing trait id with the merged description as source_text.
    embedding_sender = worker.embedding_sender  # type: ignore[attr-defined]
    trait_jobs = [p for p in embedding_sender.sent if p["record_type"] == "trait"]
    assert len(trait_jobs) == 1
    assert trait_jobs[0]["record_id"] == existing_trait_id
    assert merged_description in trait_jobs[0]["source_text"]


def test_new_trait_inserts_normally_when_no_name_match(
    db_pool,
    make_person,
    monkeypatch,
    stub_extraction_cfg,
    stub_compat_cfg,
    stub_trait_merge_cfg,
    stub_settings,
):
    """When the extracted trait name has no active match for this person,
    the worker INSERTs a new row and does NOT call the merge LLM."""
    person_id = make_person("Fresh Trait Subject")

    monkeypatch.setattr(
        ext_llm_mod, "call_with_tool", _stub_call(sample_extractions.clean_extraction())
    )
    # If the merge LLM is called, fail loudly.
    monkeypatch.setattr(
        trait_merge_mod,
        "call_with_tool",
        _stub_call({}, exc=AssertionError("merge LLM should not be called")),
    )

    worker = _build_worker(
        db_pool=db_pool,
        extraction_cfg=stub_extraction_cfg,
        compat_cfg=stub_compat_cfg,
        trait_merge_cfg=stub_trait_merge_cfg,
        settings=stub_settings,
    )
    msg = make_received_message(person_id=person_id)
    worker.process_message(msg)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM active_traits WHERE person_id=%s AND lower(name)='warmth'",
                (person_id,),
            )
            assert cur.fetchone()[0] == 1
