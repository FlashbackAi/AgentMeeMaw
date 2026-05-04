"""Tests for the per-person persistence transaction (DB-touching)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from psycopg.types.json import Json

from flashback.db.edges import EdgeValidationError, validate_edge
from flashback.workers.trait_synthesizer.persistence import (
    NewTraitRow,
    persist_synthesis,
    push_new_trait_embeddings,
)
from flashback.workers.trait_synthesizer.schema import (
    TraitSynthesisResult,
)

from tests.workers.trait_synthesizer.conftest import StubEmbeddingSender
from tests.workers.trait_synthesizer.fixtures.sample_states import (
    downgrade_decision,
    keep_decision,
    new_trait_proposal,
    synthesis_result,
    upgrade_decision,
)


# ---------------------------------------------------------------------------
# Seeding helpers (small duplicates from test_context to keep tests independent)
# ---------------------------------------------------------------------------


def _seed_trait(
    db_pool,
    *,
    person_id: str,
    name: str,
    description: str | None = "desc",
    strength: str = "mentioned_once",
    status: str = "active",
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO traits (person_id, name, description, strength, status)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (person_id, name, description, strength, status),
            )
            tid = cur.fetchone()[0]
            conn.commit()
    return tid


def _seed_thread(
    db_pool,
    *,
    person_id: str,
    name: str = "thread",
    description: str = "d",
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO threads (person_id, name, description)
                VALUES (%s, %s, %s)
                RETURNING id::text
                """,
                (person_id, name, description),
            )
            tid = cur.fetchone()[0]
            conn.commit()
    return tid


def _fetch_strength(db_pool, trait_id: str) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT strength FROM traits WHERE id = %s", (trait_id,))
            return cur.fetchone()[0]


def _count_evidence_edges(db_pool, *, thread_id: str, trait_id: str) -> int:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE from_kind='thread' AND from_id=%s
                   AND to_kind='trait'    AND to_id  =%s
                   AND edge_type='evidences'
                """,
                (thread_id, trait_id),
            )
            return cur.fetchone()[0]


def _processed_row(db_pool, key: str):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT person_id::text, traits_created, traits_upgraded,
                       traits_downgraded
                  FROM processed_trait_syntheses
                 WHERE idempotency_key = %s
                """,
                (key,),
            )
            return cur.fetchone()


def _run_in_txn(db_pool, *, person_id: str, raw: dict, key: str = None):
    """Run persist_synthesis inside a single committed transaction."""
    key = key or f"key-{uuid4()}"
    result = TraitSynthesisResult.model_validate(raw)
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                persist = persist_synthesis(
                    cur,
                    person_id=person_id,
                    result=result,
                    idempotency_key=key,
                )
    return persist, key


# ---------------------------------------------------------------------------
# Existing-trait decisions
# ---------------------------------------------------------------------------


def test_upgrade_advances_one_rung(db_pool, make_person) -> None:
    person_id = make_person("Up")
    trait_id = _seed_trait(
        db_pool, person_id=person_id, name="Patient", strength="mentioned_once"
    )
    thread_id = _seed_thread(db_pool, person_id=person_id)

    raw = synthesis_result(
        existing_decisions=[upgrade_decision(trait_id, thread_ids=[thread_id])]
    )
    persist, _ = _run_in_txn(db_pool, person_id=person_id, raw=raw)

    assert persist.upgraded_count == 1
    assert _fetch_strength(db_pool, trait_id) == "moderate"
    assert _count_evidence_edges(db_pool, thread_id=thread_id, trait_id=trait_id) == 1


def test_upgrade_at_top_is_noop_with_log(db_pool, make_person) -> None:
    person_id = make_person("Cap")
    trait_id = _seed_trait(
        db_pool, person_id=person_id, name="Defining", strength="defining"
    )
    thread_id = _seed_thread(db_pool, person_id=person_id)

    raw = synthesis_result(
        existing_decisions=[upgrade_decision(trait_id, thread_ids=[thread_id])]
    )
    persist, _ = _run_in_txn(db_pool, person_id=person_id, raw=raw)

    assert persist.upgraded_count == 0
    assert persist.skipped_at_ladder_extreme == [trait_id]
    assert _fetch_strength(db_pool, trait_id) == "defining"
    # No evidence edges for a no-op.
    assert _count_evidence_edges(db_pool, thread_id=thread_id, trait_id=trait_id) == 0


def test_downgrade_descends_one_rung(db_pool, make_person) -> None:
    person_id = make_person("Down")
    trait_id = _seed_trait(
        db_pool, person_id=person_id, name="Brave", strength="moderate"
    )
    thread_id = _seed_thread(db_pool, person_id=person_id)

    raw = synthesis_result(
        existing_decisions=[downgrade_decision(trait_id, thread_ids=[thread_id])]
    )
    persist, _ = _run_in_txn(db_pool, person_id=person_id, raw=raw)

    assert persist.downgraded_count == 1
    assert _fetch_strength(db_pool, trait_id) == "mentioned_once"
    assert _count_evidence_edges(db_pool, thread_id=thread_id, trait_id=trait_id) == 1


def test_downgrade_at_bottom_is_noop_with_log(db_pool, make_person) -> None:
    person_id = make_person("Floor")
    trait_id = _seed_trait(
        db_pool, person_id=person_id, name="Quiet", strength="mentioned_once"
    )
    raw = synthesis_result(existing_decisions=[downgrade_decision(trait_id)])
    persist, _ = _run_in_txn(db_pool, person_id=person_id, raw=raw)

    assert persist.downgraded_count == 0
    assert persist.skipped_at_ladder_extreme == [trait_id]
    assert _fetch_strength(db_pool, trait_id) == "mentioned_once"


def test_keep_is_noop(db_pool, make_person) -> None:
    person_id = make_person("Keep")
    trait_id = _seed_trait(
        db_pool, person_id=person_id, name="Steady", strength="moderate"
    )
    raw = synthesis_result(existing_decisions=[keep_decision(trait_id)])
    persist, _ = _run_in_txn(db_pool, person_id=person_id, raw=raw)

    assert persist.upgraded_count == 0
    assert persist.downgraded_count == 0
    assert _fetch_strength(db_pool, trait_id) == "moderate"


def test_unknown_trait_id_skipped(db_pool, make_person) -> None:
    """LLM hallucinates a UUID that isn't an active trait — skip silently."""
    person_id = make_person("Bogus")
    raw = synthesis_result(
        existing_decisions=[
            upgrade_decision(uuid4(), thread_ids=[uuid4()]),
        ]
    )
    persist, _ = _run_in_txn(db_pool, person_id=person_id, raw=raw)
    assert persist.upgraded_count == 0
    assert persist.downgraded_count == 0


def test_other_persons_trait_id_is_skipped(db_pool, make_person) -> None:
    """Cross-legacy guard: ids belonging to another person must not match."""
    a = make_person("A")
    b = make_person("B")
    other_id = _seed_trait(
        db_pool, person_id=b, name="Other", strength="mentioned_once"
    )
    thread_id = _seed_thread(db_pool, person_id=a)

    raw = synthesis_result(
        existing_decisions=[upgrade_decision(other_id, thread_ids=[thread_id])]
    )
    # Run as person A; the trait belongs to person B.
    persist, _ = _run_in_txn(db_pool, person_id=a, raw=raw)
    assert persist.upgraded_count == 0
    # Person B's trait was untouched.
    assert _fetch_strength(db_pool, other_id) == "mentioned_once"


# ---------------------------------------------------------------------------
# New trait proposals
# ---------------------------------------------------------------------------


def test_new_trait_inserted_with_null_embedding_columns(db_pool, make_person) -> None:
    person_id = make_person("Insert")
    thread_id = _seed_thread(db_pool, person_id=person_id)
    raw = synthesis_result(
        new_proposals=[
            new_trait_proposal(
                name="Quick to laugh",
                description="Easy laugh, lit up the room.",
                initial_strength="moderate",
                thread_ids=[thread_id],
            )
        ]
    )
    persist, _ = _run_in_txn(db_pool, person_id=person_id, raw=raw)

    assert persist.created_count == 1
    new = persist.new_traits[0]
    assert new.name == "Quick to laugh"

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT name, description, strength, description_embedding,
                          embedding_model, embedding_model_version
                     FROM traits WHERE id = %s""",
                (new.id,),
            )
            row = cur.fetchone()
    assert row[0] == "Quick to laugh"
    assert row[1] == "Easy laugh, lit up the room."
    assert row[2] == "moderate"
    assert row[3] is None
    assert row[4] is None
    assert row[5] is None
    # Evidence edge written from the supporting thread.
    assert _count_evidence_edges(db_pool, thread_id=thread_id, trait_id=new.id) == 1


def test_duplicate_name_is_skipped(db_pool, make_person) -> None:
    person_id = make_person("Dup")
    _seed_trait(db_pool, person_id=person_id, name="Generous")
    thread_id = _seed_thread(db_pool, person_id=person_id)

    raw = synthesis_result(
        new_proposals=[
            new_trait_proposal(
                name="GENEROUS",  # case-insensitive match
                description="x",
                thread_ids=[thread_id],
            )
        ]
    )
    persist, _ = _run_in_txn(db_pool, person_id=person_id, raw=raw)

    assert persist.created_count == 0
    assert persist.skipped_duplicate_names == ["GENEROUS"]


def test_evidence_edges_for_new_trait_are_written(db_pool, make_person) -> None:
    person_id = make_person("EvNew")
    t1 = _seed_thread(db_pool, person_id=person_id, name="t1")
    t2 = _seed_thread(db_pool, person_id=person_id, name="t2")

    raw = synthesis_result(
        new_proposals=[
            new_trait_proposal(
                name="Unique Trait",
                description="x",
                thread_ids=[t1, t2],
            )
        ]
    )
    persist, _ = _run_in_txn(db_pool, person_id=person_id, raw=raw)
    assert persist.new_evidence_edge_count == 2
    new_id = persist.new_traits[0].id
    assert _count_evidence_edges(db_pool, thread_id=t1, trait_id=new_id) == 1
    assert _count_evidence_edges(db_pool, thread_id=t2, trait_id=new_id) == 1


# ---------------------------------------------------------------------------
# Edge validation
# ---------------------------------------------------------------------------


def test_thread_to_trait_evidences_passes_validation(db_pool, make_person) -> None:
    """Sanity: validate_edge accepts the new tuple."""
    assert validate_edge("thread", "trait", "evidences") is None


def test_invalid_reverse_edge_still_raises() -> None:
    with pytest.raises(EdgeValidationError):
        validate_edge("trait", "thread", "evidences")


# ---------------------------------------------------------------------------
# Idempotency row + transaction atomicity
# ---------------------------------------------------------------------------


def test_idempotency_row_written(db_pool, make_person) -> None:
    person_id = make_person("Idem")
    thread_id = _seed_thread(db_pool, person_id=person_id)
    raw = synthesis_result(
        new_proposals=[
            new_trait_proposal(name="Loyal", description="x", thread_ids=[thread_id]),
        ]
    )
    _, key = _run_in_txn(db_pool, person_id=person_id, raw=raw)
    row = _processed_row(db_pool, key)
    assert row is not None
    pid, created, upgraded, downgraded = row
    assert pid == person_id
    assert created == 1
    assert upgraded == 0
    assert downgraded == 0


def test_transaction_atomicity_on_failure(db_pool, make_person) -> None:
    """If anything raises mid-persist, no rows survive."""
    person_id = make_person("Atomic")
    thread_id = _seed_thread(db_pool, person_id=person_id)
    trait_id = _seed_trait(
        db_pool, person_id=person_id, name="Existing", strength="moderate"
    )

    # Build a proposal that succeeds, then a second one that crashes the
    # transaction by referencing a malformed UUID for the supporting thread.
    raw = TraitSynthesisResult.model_validate(
        synthesis_result(
            existing_decisions=[upgrade_decision(trait_id, thread_ids=[thread_id])],
            new_proposals=[
                new_trait_proposal(
                    name="Will Survive?",
                    description="should be rolled back",
                    thread_ids=[thread_id],
                )
            ],
        )
    )

    key = f"atom-{uuid4()}"
    with pytest.raises(Exception):  # noqa: BLE001
        with db_pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    persist_synthesis(
                        cur,
                        person_id=person_id,
                        result=raw,
                        idempotency_key=key,
                    )
                    # Force a failure inside the transaction:
                    cur.execute("SELECT 1/0")

    # Trait still at its original strength; no new trait inserted.
    assert _fetch_strength(db_pool, trait_id) == "moderate"
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM traits WHERE person_id=%s AND name='Will Survive?'",
                (person_id,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT count(*) FROM processed_trait_syntheses WHERE idempotency_key=%s",
                (key,),
            )
            assert cur.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Post-commit embedding push
# ---------------------------------------------------------------------------


def test_push_new_trait_embeddings_uses_name_plus_description() -> None:
    sender = StubEmbeddingSender()
    new = [
        NewTraitRow(id="t1", name="Generous with time", description="Always sharing"),
        NewTraitRow(id="t2", name="Quiet but present", description=None),
    ]
    push_new_trait_embeddings(
        embedding_sender=sender,
        new_traits=new,
        embedding_model="voyage-3-large",
        embedding_model_version="2025-01-07",
    )
    assert len(sender.sent) == 2
    by_id = {p["record_id"]: p for p in sender.sent}
    assert by_id["t1"]["source_text"] == "Generous with time, Always sharing"
    assert by_id["t2"]["source_text"] == "Quiet but present"
    for payload in sender.sent:
        assert payload["record_type"] == "trait"
        assert payload["embedding_model"] == "voyage-3-large"
        assert payload["embedding_model_version"] == "2025-01-07"


def test_push_new_trait_embeddings_no_traits_no_calls() -> None:
    sender = StubEmbeddingSender()
    push_new_trait_embeddings(
        embedding_sender=sender,
        new_traits=[],
        embedding_model="m",
        embedding_model_version="v",
    )
    assert sender.sent == []
