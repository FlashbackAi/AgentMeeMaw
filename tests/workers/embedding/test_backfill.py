"""
Backfill CLI tests.

Like ``test_worker.py``, the DB-touching tests need
``TEST_DATABASE_URL`` pointing at Postgres + pgvector. They are
skipped otherwise via the ``schema_applied`` fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from flashback.workers.embedding.backfill import backfill

MODEL = "voyage-3-large"
VERSION = "2025-01-07"


@dataclass
class _CapturingSQS:
    sent: list[dict] = field(default_factory=list)

    def send_embedding_job(self, **kwargs) -> None:
        self.sent.append(kwargs)

    def receive(self, **_kwargs):  # pragma: no cover
        return []

    def delete(self, _rh: str) -> None:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Pure-logic test (no DB needed)
# ---------------------------------------------------------------------------


def test_dry_run_calls_no_sqs() -> None:
    """No DB scan happens when there's no pool, but we can fake one."""
    fake_pool = MagicMock()
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = [("id-1", "hello"), ("id-2", "world")]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur
    fake_pool.connection.return_value.__enter__.return_value = fake_conn

    sqs = _CapturingSQS()
    results = backfill(
        pool=fake_pool, sqs=sqs,
        embedding_model=MODEL, embedding_model_version=VERSION,
        record_types=["question"], dry_run=True,
    )

    assert sqs.sent == []
    assert results[0].record_type == "question"
    assert results[0].found == 2
    assert results[0].enqueued == 0


# ---------------------------------------------------------------------------
# DB tests
# ---------------------------------------------------------------------------


def test_seed_migration_yields_15_question_rows(db_pool):
    """The 0002 seed inserts the 15 starter_anchor rows we expect."""
    sqs = _CapturingSQS()
    results = backfill(
        pool=db_pool, sqs=sqs,
        embedding_model=MODEL, embedding_model_version=VERSION,
        record_types=["question"], dry_run=True,
    )
    [questions] = [r for r in results if r.record_type == "question"]
    assert questions.found == 15
    assert questions.enqueued == 0


def test_question_only_run_enqueues_15_with_correct_payload(db_pool):
    sqs = _CapturingSQS()
    backfill(
        pool=db_pool, sqs=sqs,
        embedding_model=MODEL, embedding_model_version=VERSION,
        record_types=["question"], dry_run=False,
    )
    assert len(sqs.sent) == 15
    sample = sqs.sent[0]
    assert set(sample) == {
        "record_type", "record_id", "source_text",
        "embedding_model", "embedding_model_version",
    }
    assert sample["record_type"] == "question"
    assert sample["embedding_model"] == MODEL
    assert sample["embedding_model_version"] == VERSION
    assert isinstance(sample["source_text"], str) and sample["source_text"]


def test_all_default_picks_up_only_questions_when_other_tables_empty(db_pool):
    """Right after migrations, only the 15 questions exist."""
    sqs = _CapturingSQS()
    results = backfill(
        pool=db_pool, sqs=sqs,
        embedding_model=MODEL, embedding_model_version=VERSION,
        record_types=None, dry_run=False,
    )
    by_type = {r.record_type: r for r in results}
    assert by_type["question"].enqueued == 15
    for kind in ("moment", "entity", "thread", "trait"):
        assert by_type[kind].found == 0
        assert by_type[kind].enqueued == 0
    assert len(sqs.sent) == 15


def test_dry_run_against_seeded_db_enqueues_nothing(db_pool):
    sqs = _CapturingSQS()
    results = backfill(
        pool=db_pool, sqs=sqs,
        embedding_model=MODEL, embedding_model_version=VERSION,
        record_types=None, dry_run=True,
    )
    assert sqs.sent == []
    by_type = {r.record_type: r for r in results}
    assert by_type["question"].found == 15
    assert by_type["question"].enqueued == 0


def test_moment_with_null_embedding_is_picked_up(db_pool, make_person):
    person_id = make_person()
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments (person_id, title, narrative, status)
                VALUES (%s, %s, %s, 'active')
                RETURNING id
                """,
                (person_id, "t", "the narrative"),
            )
            (moment_id,) = cur.fetchone()
            conn.commit()

    sqs = _CapturingSQS()
    backfill(
        pool=db_pool, sqs=sqs,
        embedding_model=MODEL, embedding_model_version=VERSION,
        record_types=["moment"], dry_run=False,
    )
    assert len(sqs.sent) == 1
    assert sqs.sent[0]["record_id"] == str(moment_id)
    assert sqs.sent[0]["source_text"] == "the narrative"


def test_trait_source_expression_handles_null_description(db_pool, make_person):
    """traits.description is nullable; the source expr must still produce text."""
    person_id = make_person()
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO traits (person_id, name, description, status)
                VALUES (%s, 'kind', NULL, 'active')
                RETURNING id
                """,
                (person_id,),
            )
            (trait_id,) = cur.fetchone()
            conn.commit()

    sqs = _CapturingSQS()
    backfill(
        pool=db_pool, sqs=sqs,
        embedding_model=MODEL, embedding_model_version=VERSION,
        record_types=["trait"], dry_run=False,
    )
    assert len(sqs.sent) == 1
    assert sqs.sent[0]["record_id"] == str(trait_id)
    assert sqs.sent[0]["source_text"] == "kind"


def test_thread_source_expression_concatenates_name_and_description(
    db_pool, make_person,
):
    person_id = make_person()
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO threads (person_id, name, description, source, status)
                VALUES (%s, 'Mornings', 'how he started his day', 'manual', 'active')
                RETURNING id
                """,
                (person_id,),
            )
            (thread_id,) = cur.fetchone()
            conn.commit()

    sqs = _CapturingSQS()
    backfill(
        pool=db_pool, sqs=sqs,
        embedding_model=MODEL, embedding_model_version=VERSION,
        record_types=["thread"], dry_run=False,
    )
    assert len(sqs.sent) == 1
    assert sqs.sent[0]["source_text"] == "Mornings, how he started his day"


def test_already_embedded_rows_are_skipped(db_pool, make_person):
    person_id = make_person()
    vec = [0.1] * 1024
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entities
                    (person_id, kind, name, description, status,
                     description_embedding, embedding_model,
                     embedding_model_version)
                VALUES (%s, 'person', 'Alice', 'desc', 'active',
                        %s, 'voyage-3-large', '2025-01-07')
                """,
                (person_id, vec),
            )
            cur.execute(
                """
                INSERT INTO entities
                    (person_id, kind, name, description, status)
                VALUES (%s, 'place', 'Brooklyn', 'a borough', 'active')
                """,
                (person_id,),
            )
            conn.commit()

    sqs = _CapturingSQS()
    backfill(
        pool=db_pool, sqs=sqs,
        embedding_model=MODEL, embedding_model_version=VERSION,
        record_types=["entity"], dry_run=False,
    )
    assert len(sqs.sent) == 1
    assert sqs.sent[0]["source_text"] == "a borough"
