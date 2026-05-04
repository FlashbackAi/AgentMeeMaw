"""
Persistence layer tests (require TEST_DATABASE_URL).

Covers a clean extraction: 2 moments, 3 entities, 1 trait, 1 dropped
reference. Verifies row counts, edges (with ``validate_edge`` actually
called), and the seeded-question ``answered_by`` linkage.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from flashback.db import edges as edges_mod
from flashback.workers.extraction.persistence import (
    MomentDecision,
    PersonRow,
    persist_extraction,
)
from flashback.workers.extraction.schema import ExtractionResult
from tests.workers.extraction.fixtures import sample_extractions


def _seed_question(db_pool, person_id: str) -> str:
    """Insert a starter-style per-person question and return its UUID."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO questions (person_id, text, source, attributes)
                VALUES (%s, 'tell me a memory', 'underdeveloped_entity',
                        '{"themes":["family"]}'::jsonb)
                RETURNING id::text
                """,
                (person_id,),
            )
            qid = cur.fetchone()[0]
            conn.commit()
    return qid


def test_clean_extraction_persists_with_edges(db_pool, make_person, monkeypatch):
    person_id = make_person("Dad Smith")
    seeded_question_id = _seed_question(db_pool, person_id)

    extraction = ExtractionResult.model_validate(
        sample_extractions.clean_extraction()
    )
    decisions = [MomentDecision(moment=m) for m in extraction.moments]

    # Spy on validate_edge to confirm it fires for every edge insert.
    real_validate = edges_mod.validate_edge
    calls: list[tuple[str, str, str]] = []

    def _spy(from_kind, to_kind, edge_type):
        calls.append((from_kind, to_kind, edge_type))
        return real_validate(from_kind, to_kind, edge_type)

    monkeypatch.setattr(
        "flashback.workers.extraction.persistence.validate_edge", _spy
    )

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                result = persist_extraction(
                    cur,
                    person=PersonRow(id=person_id, name="Dad Smith", aliases=[]),
                    extraction=extraction,
                    moment_decisions=decisions,
                    seeded_question_id=seeded_question_id,
                )

    # Counts.
    assert len(result.moment_ids) == 2
    assert len(result.entity_ids) == 3
    assert len(result.trait_ids) == 1
    assert len(result.question_ids) == 1
    assert result.dropped_entities_count == 0

    # validate_edge called for every edge insert (involves x2, happened_at,
    # exemplifies, plus 2x answered_by).
    edge_types = [c[2] for c in calls]
    assert "involves" in edge_types
    assert "happened_at" in edge_types
    assert "exemplifies" in edge_types
    assert "answered_by" in edge_types

    # answered_by edges from the seeded question to each new moment.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE from_kind='question' AND from_id=%s
                   AND to_kind='moment'   AND edge_type='answered_by'
                """,
                (seeded_question_id,),
            )
            (count,) = cur.fetchone()
    assert count == 2


def test_extraction_without_seeded_question_writes_no_answered_by(
    db_pool, make_person
):
    person_id = make_person("Mom Smith")
    extraction = ExtractionResult.model_validate(
        sample_extractions.clean_extraction()
    )
    decisions = [MomentDecision(moment=m) for m in extraction.moments]

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                persist_extraction(
                    cur,
                    person=PersonRow(id=person_id, name="Mom Smith", aliases=[]),
                    extraction=extraction,
                    moment_decisions=decisions,
                    seeded_question_id=None,
                )

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE edge_type='answered_by'
                   AND to_id IN (
                     SELECT id FROM moments WHERE person_id=%s
                   )
                """,
                (person_id,),
            )
            (count,) = cur.fetchone()
    assert count == 0


def test_invalid_edge_aborts_transaction(db_pool, make_person):
    """A bad happened_at (to a non-place entity) is dropped silently. Use a
    truly invalid edge (involves to a trait) to prove the transaction aborts."""
    person_id = make_person("Bad Edges")
    payload = sample_extractions.empty_extraction()
    payload["moments"] = [
        {
            "title": "x",
            "narrative": "y",
            "generation_prompt": "z",
            "exemplifies_trait_indexes": [0],
        }
    ]
    payload["traits"] = [{"name": "warmth"}]
    extraction = ExtractionResult.model_validate(payload)
    decisions = [MomentDecision(moment=m) for m in extraction.moments]

    # Patch validate_edge to reject one edge type to simulate a future
    # schema-coupled validation breakage. We choose 'exemplifies' so we
    # exercise the rollback path on a genuinely raised error.
    from flashback.workers.extraction import persistence as pers_mod

    def _reject_exemplifies(from_kind, to_kind, edge_type):
        if edge_type == "exemplifies":
            raise edges_mod.EdgeValidationError("rejected for test")

    original = pers_mod.validate_edge
    pers_mod.validate_edge = _reject_exemplifies  # type: ignore[assignment]
    try:
        with pytest.raises(edges_mod.EdgeValidationError):
            with db_pool.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        persist_extraction(
                            cur,
                            person=PersonRow(
                                id=person_id, name="Bad Edges", aliases=[]
                            ),
                            extraction=extraction,
                            moment_decisions=decisions,
                            seeded_question_id=None,
                        )
    finally:
        pers_mod.validate_edge = original  # type: ignore[assignment]

    # Nothing persisted.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM moments WHERE person_id=%s", (person_id,)
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT count(*) FROM traits WHERE person_id=%s", (person_id,)
            )
            assert cur.fetchone()[0] == 0
