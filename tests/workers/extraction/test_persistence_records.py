"""SP5: persist_extraction writes same-event link + contradiction rows, and
supersession repoints them."""

from __future__ import annotations

from flashback.moment_links import insert_same_event_link
from flashback.workers.extraction.persistence import (
    MomentDecision,
    PersonRow,
    persist_extraction,
)
from flashback.workers.extraction.schema import ExtractionResult
from tests.workers.extraction.fixtures import sample_extractions


def _insert_moment(db_pool, person_id: str, title: str) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO moments (person_id, title, narrative) "
                "VALUES (%s, %s, 'n') RETURNING id::text",
                (person_id, title),
            )
            mid = cur.fetchone()[0]
            conn.commit()
    return mid


def _run(db_pool, person_id, decisions, extraction):
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                return persist_extraction(
                    cur,
                    person=PersonRow(id=person_id, name="Subj", aliases=[]),
                    extraction=extraction,
                    moment_decisions=decisions,
                    seeded_question_id=None,
                )


def test_same_event_decision_writes_link(db_pool, make_person):
    person_id = make_person("Subj")
    existing = _insert_moment(db_pool, person_id, "existing")

    extraction = ExtractionResult.model_validate(sample_extractions.clean_extraction())
    decisions = [MomentDecision(moment=m) for m in extraction.moments]
    decisions[0].same_event_ids = [existing]

    result = _run(db_pool, person_id, decisions, extraction)
    new_id = result.moment_ids[0]

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT moment_a_id::text, moment_b_id::text, status "
                "FROM moment_same_event_links WHERE person_id = %s",
                (person_id,),
            )
            rows = cur.fetchall()
    assert len(rows) == 1
    a, b, status = rows[0]
    assert status == "active"
    assert {a, b} == {new_id, existing}


def test_contradiction_decision_writes_pending_row(db_pool, make_person):
    person_id = make_person("Subj")
    existing = _insert_moment(db_pool, person_id, "existing")

    extraction = ExtractionResult.model_validate(sample_extractions.clean_extraction())
    decisions = [MomentDecision(moment=m) for m in extraction.moments]
    decisions[0].contradicts_ids = [existing]

    result = _run(db_pool, person_id, decisions, extraction)
    new_id = result.moment_ids[0]

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT moment_a_id::text, moment_b_id::text, status "
                "FROM moment_contradictions WHERE person_id = %s",
                (person_id,),
            )
            rows = cur.fetchall()
    assert len(rows) == 1
    a, b, status = rows[0]
    assert status == "pending"
    assert {a, b} == {new_id, existing}


def test_supersession_repoints_active_link(db_pool, make_person):
    """When a linked moment is superseded, its active link follows to the new id."""
    person_id = make_person("Subj")
    old = _insert_moment(db_pool, person_id, "old")
    partner = _insert_moment(db_pool, person_id, "partner")

    # Seed an active link between `old` and `partner`.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            lid = insert_same_event_link(
                cur, person_id=person_id, moment_a_id=old, moment_b_id=partner, reason="r"
            )
            conn.commit()

    # A new extracted moment supersedes `old`.
    extraction = ExtractionResult.model_validate(sample_extractions.clean_extraction())
    decisions = [MomentDecision(moment=m) for m in extraction.moments]
    decisions[0].supersedes_id = old
    result = _run(db_pool, person_id, decisions, extraction)
    new_id = result.moment_ids[0]

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT moment_a_id::text, moment_b_id::text, status "
                "FROM moment_same_event_links WHERE id = %s",
                (lid,),
            )
            a, b, status = cur.fetchone()
    assert status == "active"
    assert {a, b} == {new_id, partner}
