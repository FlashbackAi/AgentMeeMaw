"""End-to-end runner tests (DB-touching, LLM stubbed)."""

from __future__ import annotations

from uuid import uuid4

from flashback.workers.profile_summary import summary_llm as summary_mod
from flashback.workers.profile_summary.runner import run_once

from tests.workers.profile_summary.conftest import queued_call_text
from tests.workers.profile_summary.fixtures.sample_profiles import (
    seed_rich_profile,
)


def _profile_summary(db_pool, person_id: str) -> str | None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT profile_summary FROM persons WHERE id=%s", (person_id,)
            )
            row = cur.fetchone()
    return row[0] if row else None


def _idem_chars(db_pool, key: str) -> int | None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT summary_chars
                     FROM processed_profile_summaries
                    WHERE idempotency_key=%s""",
                (key,),
            )
            row = cur.fetchone()
    return row[0] if row else None


def test_run_once_happy_path_writes_summary_and_idempotency(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings, top_caps
):
    person_id = make_person("RichLegacy")
    seed_rich_profile(db_pool, person_id)

    monkeypatch.setattr(
        summary_mod,
        "call_text",
        queued_call_text(
            ["RichLegacy was a quiet, generous man who taught patiently."]
        ),
    )

    key = f"k-{uuid4()}"
    result = run_once(
        db_pool=db_pool,
        summary_cfg=stub_summary_cfg,
        settings=stub_settings,
        person_id=person_id,
        idempotency_key=key,
        **top_caps,
    )

    assert result.skipped is False
    assert result.empty is False
    assert result.persist is not None
    assert result.persist.summary_chars > 0

    saved = _profile_summary(db_pool, person_id)
    assert saved == "RichLegacy was a quiet, generous man who taught patiently."
    assert _idem_chars(db_pool, key) == len(saved)


def test_run_once_idempotent_on_same_key(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings, top_caps
):
    person_id = make_person("Idem")
    seed_rich_profile(db_pool, person_id)

    # Only one LLM response queued. The second run must hit the
    # idempotency short-circuit and not pop from the queue.
    monkeypatch.setattr(
        summary_mod,
        "call_text",
        queued_call_text(["First summary."]),
    )

    key = f"shared-{uuid4()}"
    first = run_once(
        db_pool=db_pool,
        summary_cfg=stub_summary_cfg,
        settings=stub_settings,
        person_id=person_id,
        idempotency_key=key,
        **top_caps,
    )
    second = run_once(
        db_pool=db_pool,
        summary_cfg=stub_summary_cfg,
        settings=stub_settings,
        person_id=person_id,
        idempotency_key=key,
        **top_caps,
    )

    assert first.skipped is False
    assert second.skipped is True
    assert _profile_summary(db_pool, person_id) == "First summary."


def test_run_once_different_keys_overwrite_each_other(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings, top_caps
):
    """Two distinct keys → two distinct runs that overwrite the
    profile_summary text. This is the desired behavior — summaries get
    fresher as more is recorded."""
    person_id = make_person("Fresh")
    seed_rich_profile(db_pool, person_id)

    monkeypatch.setattr(
        summary_mod,
        "call_text",
        queued_call_text(["older summary text", "newer summary text"]),
    )

    for _ in range(2):
        run_once(
            db_pool=db_pool,
            summary_cfg=stub_summary_cfg,
            settings=stub_settings,
            person_id=person_id,
            idempotency_key=f"k-{uuid4()}",
            **top_caps,
        )

    assert _profile_summary(db_pool, person_id) == "newer summary text"


def test_run_once_empty_legacy_short_circuits(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings, top_caps
):
    """No traits, threads, or entities → no LLM call, no summary, but
    idempotency row written with chars=0."""
    person_id = make_person("Empty")

    # If the LLM is called, this will raise.
    def _boom(**kwargs):
        raise AssertionError("LLM must not be called for an empty legacy")

    monkeypatch.setattr(summary_mod, "call_text", _boom)

    key = f"empty-{uuid4()}"
    result = run_once(
        db_pool=db_pool,
        summary_cfg=stub_summary_cfg,
        settings=stub_settings,
        person_id=person_id,
        idempotency_key=key,
        **top_caps,
    )

    assert result.skipped is False
    assert result.empty is True
    assert _profile_summary(db_pool, person_id) is None
    assert _idem_chars(db_pool, key) == 0


def test_run_once_empty_legacy_then_populated_legacy_writes_summary(
    db_pool, make_person, monkeypatch, stub_summary_cfg, stub_settings, top_caps
):
    """Empty-legacy run logs idempotency. A *new* key on a *populated*
    legacy proceeds to the LLM and writes the summary."""
    person_id = make_person("LaterFull")

    monkeypatch.setattr(
        summary_mod,
        "call_text",
        queued_call_text(["Now there's something to say."]),
    )

    # First run while legacy is empty.
    run_once(
        db_pool=db_pool,
        summary_cfg=stub_summary_cfg,
        settings=stub_settings,
        person_id=person_id,
        idempotency_key=f"first-{uuid4()}",
        **top_caps,
    )
    assert _profile_summary(db_pool, person_id) is None

    # Now populate.
    seed_rich_profile(db_pool, person_id)

    # Second run with a new key — produces a summary.
    run_once(
        db_pool=db_pool,
        summary_cfg=stub_summary_cfg,
        settings=stub_settings,
        person_id=person_id,
        idempotency_key=f"second-{uuid4()}",
        **top_caps,
    )
    assert _profile_summary(db_pool, person_id) == "Now there's something to say."
