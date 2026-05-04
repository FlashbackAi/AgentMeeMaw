"""Persistence tests for producer questions."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from flashback.db.edges import EdgeValidationError
from flashback.workers.producers.persistence import (
    _insert_validated_edge,
    persist_producer_result,
    push_question_embeddings,
)
from flashback.workers.producers.schema import GeneratedQuestion, ProducerResult

from tests.workers.producers.conftest import StubEmbeddingSender, seed_entity


def _fetch_questions(db_pool, person_id: str):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, source, attributes
                  FROM questions
                 WHERE person_id = %s
                 ORDER BY created_at
                """,
                (person_id,),
            )
            return cur.fetchall()


def _edge_count(db_pool, *, question_id: str) -> int:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                  FROM edges
                 WHERE from_kind='question'
                   AND from_id=%s
                """,
                (question_id,),
            )
            return int(cur.fetchone()[0])


def test_p2_writes_question_and_targets_edge(db_pool, make_person):
    person_id = make_person("P2 persist")
    entity_id = seed_entity(db_pool, person_id=person_id, name="Auntie")
    result = ProducerResult(
        person_id=UUID(person_id),
        source_tag="underdeveloped_entity",
        overall_reasoning="x",
        questions=[
            GeneratedQuestion(
                text="What made Auntie memorable?",
                themes=["family"],
                attributes={},
                targets_entity_id=UUID(entity_id),
            )
        ],
    )

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                persist = persist_producer_result(cur, result=result)

    rows = _fetch_questions(db_pool, person_id)
    assert rows[0][1] == "underdeveloped_entity"
    assert rows[0][2]["themes"] == ["family"]
    assert _edge_count(db_pool, question_id=persist.question_ids[0]) == 1


def test_p3_and_p5_write_attributes_without_edges(db_pool, make_person):
    person_id = make_person("P3 P5 persist")
    for source, attrs in [
        ("life_period_gap", {"life_period": "1960s"}),
        ("universal_dimension", {"dimension": "food"}),
    ]:
        result = ProducerResult(
            person_id=UUID(person_id),
            source_tag=source,  # type: ignore[arg-type]
            overall_reasoning="x",
            questions=[
                GeneratedQuestion(
                    text=f"Question for {source}?",
                    themes=["theme"],
                    attributes=attrs,
                )
            ],
        )
        with db_pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    persist = persist_producer_result(cur, result=result)
        assert _edge_count(db_pool, question_id=persist.question_ids[0]) == 0

    rows = _fetch_questions(db_pool, person_id)
    assert rows[0][2]["life_period"] == "1960s"
    assert rows[1][2]["dimension"] == "food"
    assert all(row[2]["themes"] for row in rows)


def test_embedding_push_one_per_question(make_person):
    person_id = make_person("Embedding")
    result = ProducerResult(
        person_id=UUID(person_id),
        source_tag="life_period_gap",
        overall_reasoning="x",
        questions=[
            GeneratedQuestion(text="Q1?", themes=["a"], attributes={"life_period": "x"}),
            GeneratedQuestion(text="Q2?", themes=["b"], attributes={"life_period": "x"}),
        ],
    )
    sender = StubEmbeddingSender()

    push_question_embeddings(
        embedding_sender=sender,
        result=result,
        question_ids=[str(uuid4()), str(uuid4())],
        embedding_model="voyage-3-large",
        embedding_model_version="2025-01-07",
    )

    assert [job["record_type"] for job in sender.sent] == ["question", "question"]
    assert [job["source_text"] for job in sender.sent] == ["Q1?", "Q2?"]


def test_transaction_atomicity(db_pool, make_person):
    person_id = make_person("Atomic")
    result = ProducerResult(
        person_id=UUID(person_id),
        source_tag="underdeveloped_entity",
        overall_reasoning="x",
        questions=[
            GeneratedQuestion(
                text="Bad target?",
                themes=["x"],
                attributes={},
                targets_entity_id=uuid4(),
            )
        ],
    )

    with pytest.raises(Exception):
        with db_pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    persist_producer_result(cur, result=result)

    assert _fetch_questions(db_pool, person_id) == []


def test_edge_validation_rejects_invalid_combination(db_pool):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            with pytest.raises(EdgeValidationError):
                _insert_validated_edge(
                    cur,
                    from_kind="question",
                    from_id=str(uuid4()),
                    to_kind="moment",
                    to_id=str(uuid4()),
                    edge_type="targets",
                )

