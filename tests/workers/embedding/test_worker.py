"""
Worker drain-loop tests.

The DB-touching tests use the ``db_pool`` fixture from
``tests/conftest.py``, which requires ``TEST_DATABASE_URL`` to point
at a Postgres instance with pgvector. They are skipped otherwise.

Two tests in here do not need a database (the Voyage-failure and
DB-failure paths) - those use a stub pool that raises, so they
always run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest
from psycopg import errors as psycopg_errors

from flashback.workers.embedding import worker as worker_mod
from flashback.workers.embedding.sqs_client import EmbeddingMessage
from flashback.workers.embedding.voyage_client import VoyageClient, VoyageError

MODEL = "voyage-3-large"
VERSION = "2025-01-07"
DIM = 1024


def _vec(seed: float = 0.01) -> list[float]:
    return [seed] * DIM


def _msg(record_type: str, record_id: str, *, model: str = MODEL,
         version: str = VERSION, source_text: str = "hello",
         receipt: str = "rh-1") -> EmbeddingMessage:
    return EmbeddingMessage(
        record_type=record_type,
        record_id=record_id,
        source_text=source_text,
        embedding_model=model,
        embedding_model_version=version,
        receipt_handle=receipt,
        raw_body=json.dumps({"x": 1}),
    )


@dataclass
class _FakeSQS:
    deleted: list[str] = field(default_factory=list)

    def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)

    def receive(self, *, max_messages, wait_seconds):  # pragma: no cover
        return []

    def send_embedding_job(self, **_kwargs) -> None:  # pragma: no cover
        pass


class _StubVoyage(VoyageClient):
    """Voyage stub that returns canned vectors or raises VoyageError."""

    def __init__(self, *, vectors=None, raise_with: Exception | None = None) -> None:
        super().__init__(api_key="unused", _client=MagicMock())
        self._stub_vectors = vectors
        self._stub_raise = raise_with
        self.calls: list[tuple[list[str], str]] = []

    def embed_batch(self, texts, model):  # type: ignore[override]
        self.calls.append((list(texts), model))
        if self._stub_raise is not None:
            raise self._stub_raise
        return self._stub_vectors or [_vec(0.5) for _ in texts]


# ---------------------------------------------------------------------------
# DB-touching tests (require TEST_DATABASE_URL)
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_moment(db_pool, make_person):
    """Insert one moment row, returning (person_id, moment_id)."""

    def _seed(*, narrative: str = "the porch at dusk",
              status: str = "active", model: str | None = None,
              version: str | None = None) -> tuple[str, str]:
        person_id = make_person()
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                if model is None:
                    cur.execute(
                        """
                        INSERT INTO moments
                            (person_id, title, narrative, status)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id
                        """,
                        (person_id, "title", narrative, status),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO moments
                            (person_id, title, narrative, status,
                             narrative_embedding,
                             embedding_model, embedding_model_version)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            person_id, "title", narrative, status,
                            _vec(0.99), model, version,
                        ),
                    )
                (moment_id,) = cur.fetchone()
                conn.commit()
        return person_id, str(moment_id)

    return _seed


def _moment_row(db_pool, moment_id: str):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT narrative_embedding IS NOT NULL,
                       embedding_model, embedding_model_version
                  FROM moments WHERE id = %s
                """,
                (moment_id,),
            )
            return cur.fetchone()


def test_happy_path_writes_vector_and_acks(db_pool, seed_moment):
    _, moment_id = seed_moment()
    sqs = _FakeSQS()
    voyage = _StubVoyage(vectors=[_vec(0.42)])

    worker_mod.process_batch(
        [_msg("moment", moment_id)],
        pool=db_pool, voyage=voyage, sqs=sqs,
    )

    has_vec, model, version = _moment_row(db_pool, moment_id)
    assert has_vec is True
    assert model == MODEL
    assert version == VERSION
    assert sqs.deleted == ["rh-1"]


def test_first_time_backfill_sets_all_three_columns(db_pool, seed_moment):
    """The CHECK constraint refuses partial state; this exercises it."""
    _, moment_id = seed_moment()
    sqs = _FakeSQS()
    voyage = _StubVoyage(vectors=[_vec(0.1)])

    worker_mod.process_batch(
        [_msg("moment", moment_id)],
        pool=db_pool, voyage=voyage, sqs=sqs,
    )

    has_vec, model, version = _moment_row(db_pool, moment_id)
    assert has_vec and model is not None and version is not None


def test_version_guard_skips_when_row_already_on_different_model(db_pool, seed_moment):
    _, moment_id = seed_moment(model="old-model", version="2024-01-01")
    sqs = _FakeSQS()
    voyage = _StubVoyage(vectors=[_vec(0.7)])

    worker_mod.process_batch(
        [_msg("moment", moment_id, model=MODEL, version=VERSION)],
        pool=db_pool, voyage=voyage, sqs=sqs,
    )

    has_vec, model, version = _moment_row(db_pool, moment_id)
    assert has_vec is True  # original vector still in place
    assert model == "old-model"
    assert version == "2024-01-01"
    # Acked anyway: stale work, no point retrying.
    assert sqs.deleted == ["rh-1"]


def test_status_guard_skips_superseded_row(db_pool, seed_moment):
    _, moment_id = seed_moment(status="superseded")
    sqs = _FakeSQS()
    voyage = _StubVoyage(vectors=[_vec(0.9)])

    worker_mod.process_batch(
        [_msg("moment", moment_id)],
        pool=db_pool, voyage=voyage, sqs=sqs,
    )

    has_vec, model, version = _moment_row(db_pool, moment_id)
    assert (has_vec, model, version) == (False, None, None)
    assert sqs.deleted == ["rh-1"]


def test_same_model_re_embed_overwrites_vector(db_pool, seed_moment):
    _, moment_id = seed_moment(model=MODEL, version=VERSION)
    sqs = _FakeSQS()
    new_vec = _vec(0.123)
    voyage = _StubVoyage(vectors=[new_vec])

    worker_mod.process_batch(
        [_msg("moment", moment_id)],
        pool=db_pool, voyage=voyage, sqs=sqs,
    )

    has_vec, model, version = _moment_row(db_pool, moment_id)
    assert has_vec is True
    assert model == MODEL and version == VERSION
    assert sqs.deleted == ["rh-1"]


# ---------------------------------------------------------------------------
# Failure-path tests (no DB needed)
# ---------------------------------------------------------------------------


def test_voyage_failure_does_not_ack_anything() -> None:
    sqs = _FakeSQS()
    voyage = _StubVoyage(raise_with=VoyageError("boom"))

    pool_should_not_be_touched = MagicMock()

    worker_mod.process_batch(
        [_msg("moment", "00000000-0000-0000-0000-000000000001")],
        pool=pool_should_not_be_touched, voyage=voyage, sqs=sqs,
    )
    assert sqs.deleted == []
    pool_should_not_be_touched.connection.assert_not_called()


def test_db_failure_does_not_ack_message() -> None:
    sqs = _FakeSQS()
    voyage = _StubVoyage(vectors=[_vec(0.1)])

    failing_pool = MagicMock()
    failing_conn = MagicMock()
    failing_cur = MagicMock()
    failing_cur.execute.side_effect = psycopg_errors.OperationalError("conn lost")
    failing_conn.cursor.return_value.__enter__.return_value = failing_cur
    failing_pool.connection.return_value.__enter__.return_value = failing_conn

    worker_mod.process_batch(
        [_msg("moment", "00000000-0000-0000-0000-000000000002")],
        pool=failing_pool, voyage=voyage, sqs=sqs,
    )
    assert sqs.deleted == []


def test_unknown_record_type_is_acked_and_dropped() -> None:
    """Defensive: a malformed message should not poison the worker."""
    sqs = _FakeSQS()
    voyage = _StubVoyage()

    worker_mod.process_batch(
        [_msg("not_a_real_type", "00000000-0000-0000-0000-000000000003",
              receipt="bad-rh")],
        pool=MagicMock(), voyage=voyage, sqs=sqs,
    )
    assert sqs.deleted == ["bad-rh"]
    assert voyage.calls == []


def test_messages_grouped_by_model_make_one_voyage_call_each() -> None:
    """Two messages, two distinct (model, version) pairs => two batches."""
    sqs = _FakeSQS()
    voyage = _StubVoyage(raise_with=VoyageError("stop after grouping"))

    worker_mod.process_batch(
        [
            _msg("moment", "00000000-0000-0000-0000-000000000001",
                 model="v1", version="a", receipt="rh-1"),
            _msg("moment", "00000000-0000-0000-0000-000000000002",
                 model="v2", version="b", receipt="rh-2"),
        ],
        pool=MagicMock(), voyage=voyage, sqs=sqs,
    )
    assert len(voyage.calls) == 2
    models_called = {model for _, model in voyage.calls}
    assert models_called == {"v1", "v2"}
    assert sqs.deleted == []  # voyage failed both batches
