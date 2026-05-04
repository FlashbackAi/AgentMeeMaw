"""End-to-end runner tests (DB-touching, LLM stubbed)."""

from __future__ import annotations

from uuid import uuid4

from flashback.workers.trait_synthesizer import synth_llm as synth_mod
from flashback.workers.trait_synthesizer.runner import run_once

from tests.workers.trait_synthesizer.conftest import (
    StubEmbeddingSender,
    queued_call_with_tool,
)
from tests.workers.trait_synthesizer.fixtures.sample_states import (
    new_trait_proposal,
    synthesis_result,
    upgrade_decision,
)


MODEL = "voyage-3-large"
VERSION = "2025-01-07"


def _seed_trait(db_pool, *, person_id, name, strength="mentioned_once"):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO traits (person_id, name, description, strength)
                VALUES (%s, %s, %s, %s) RETURNING id::text
                """,
                (person_id, name, "desc", strength),
            )
            tid = cur.fetchone()[0]
            conn.commit()
    return tid


def _seed_thread(db_pool, *, person_id, name="thread"):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO threads (person_id, name, description)
                VALUES (%s, %s, %s) RETURNING id::text
                """,
                (person_id, name, "d"),
            )
            tid = cur.fetchone()[0]
            conn.commit()
    return tid


def test_run_once_happy_path_writes_and_pushes(
    db_pool, make_person, monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    person_id = make_person("Happy")
    trait_id = _seed_trait(db_pool, person_id=person_id, name="Generous")
    thread_id = _seed_thread(db_pool, person_id=person_id, name="Cabin summers")

    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                synthesis_result(
                    existing_decisions=[
                        upgrade_decision(trait_id, thread_ids=[thread_id])
                    ],
                    new_proposals=[
                        new_trait_proposal(
                            name="Quick to laugh",
                            description="Easy laugh",
                            thread_ids=[thread_id],
                        )
                    ],
                )
            ]
        ),
    )

    sender = StubEmbeddingSender()
    result = run_once(
        db_pool=db_pool,
        embedding_sender=sender,
        synth_cfg=stub_synth_cfg,
        settings=stub_settings,
        person_id=person_id,
        idempotency_key=f"k-{uuid4()}",
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )
    assert result.skipped is False
    assert result.persist is not None
    assert result.persist.upgraded_count == 1
    assert result.persist.created_count == 1
    # Embedding push happens once per NEW trait only (existing strength
    # change is not re-embedded).
    assert len(sender.sent) == 1
    assert sender.sent[0]["record_type"] == "trait"


def test_run_once_idempotent_on_same_key(
    db_pool, make_person, monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    person_id = make_person("Idem-runner")
    thread_id = _seed_thread(db_pool, person_id=person_id)

    # The first call uses the LLM; the second must NOT — it should hit
    # the idempotency short-circuit. Queue a single response and assert
    # no second call is attempted.
    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                synthesis_result(
                    new_proposals=[
                        new_trait_proposal(
                            name="Singular Trait",
                            description="x",
                            thread_ids=[thread_id],
                        )
                    ]
                ),
            ]
        ),
    )

    key = f"shared-{uuid4()}"
    sender = StubEmbeddingSender()

    first = run_once(
        db_pool=db_pool,
        embedding_sender=sender,
        synth_cfg=stub_synth_cfg,
        settings=stub_settings,
        person_id=person_id,
        idempotency_key=key,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )
    second = run_once(
        db_pool=db_pool,
        embedding_sender=sender,
        synth_cfg=stub_synth_cfg,
        settings=stub_settings,
        person_id=person_id,
        idempotency_key=key,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )

    assert first.skipped is False
    assert second.skipped is True
    # Embedding push happened once total.
    assert len(sender.sent) == 1
    # Only one trait row exists.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM traits WHERE person_id=%s AND name='Singular Trait'",
                (person_id,),
            )
            assert cur.fetchone()[0] == 1


def test_run_once_different_keys_for_same_person_each_runs(
    db_pool, make_person, monkeypatch, stub_synth_cfg, stub_settings
) -> None:
    """Two distinct keys → two distinct runs (e.g., two session wraps)."""
    person_id = make_person("MultiKey")
    thread_id = _seed_thread(db_pool, person_id=person_id)

    monkeypatch.setattr(
        synth_mod,
        "call_with_tool",
        queued_call_with_tool(
            [
                synthesis_result(
                    new_proposals=[
                        new_trait_proposal(
                            name="First Trait",
                            description="x",
                            thread_ids=[thread_id],
                        )
                    ]
                ),
                synthesis_result(
                    new_proposals=[
                        new_trait_proposal(
                            name="Second Trait",
                            description="y",
                            thread_ids=[thread_id],
                        )
                    ]
                ),
            ]
        ),
    )

    sender = StubEmbeddingSender()
    for _ in range(2):
        run_once(
            db_pool=db_pool,
            embedding_sender=sender,
            synth_cfg=stub_synth_cfg,
            settings=stub_settings,
            person_id=person_id,
            idempotency_key=f"k-{uuid4()}",
            embedding_model=MODEL,
            embedding_model_version=VERSION,
        )
    # Two rows + two embedding pushes.
    assert len(sender.sent) == 2
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM traits WHERE person_id=%s AND name LIKE %s",
                (person_id, "%Trait"),
            )
            assert cur.fetchone()[0] == 2
