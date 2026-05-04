"""Subject-guard test: extracted entities matching persons.name are dropped."""

from __future__ import annotations

from flashback.workers.extraction.persistence import (
    MomentDecision,
    PersonRow,
    persist_extraction,
)
from flashback.workers.extraction.schema import ExtractionResult
from tests.workers.extraction.fixtures import sample_extractions


def test_subject_self_reference_dropped(db_pool, make_person):
    person_id = make_person("Test Subject")
    payload = sample_extractions.extraction_with_subject_self_reference()
    extraction = ExtractionResult.model_validate(payload)
    decisions = [MomentDecision(moment=m) for m in extraction.moments]

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                result = persist_extraction(
                    cur,
                    person=PersonRow(
                        id=person_id, name="Test Subject", aliases=[]
                    ),
                    extraction=extraction,
                    moment_decisions=decisions,
                    seeded_question_id=None,
                )

    assert result.dropped_entities_count == 1
    # Only the surviving "Old farmhouse" entity should exist for this person.
    assert len(result.entity_ids) == 1
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM entities WHERE person_id=%s", (person_id,)
            )
            names = [r[0] for r in cur.fetchall()]
    assert names == ["Old farmhouse"]

    # The moment's involves_entity_indexes referenced [0,1]; index 0 was
    # the dropped subject self-reference, so the moment ends up with one
    # involves edge (to the surviving farmhouse), not two.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE from_kind='moment'
                   AND edge_type='involves'
                   AND from_id IN (SELECT id FROM moments WHERE person_id=%s)
                """,
                (person_id,),
            )
            (involves_count,) = cur.fetchone()
    assert involves_count == 1


def test_subject_alias_match_drops_entity(db_pool, make_person):
    """Aliases on the PersonRow also drop matching extractions."""
    person_id = make_person("Margaret Smith")
    payload = sample_extractions.empty_extraction()
    payload["moments"] = [
        {
            "title": "x",
            "narrative": "y",
            "generation_prompt": "z",
            "involves_entity_indexes": [0],
        }
    ]
    payload["entities"] = [
        {
            "kind": "person",
            "name": "Maggie",
            "generation_prompt": "p",
            "description": "she",
        }
    ]
    extraction = ExtractionResult.model_validate(payload)
    decisions = [MomentDecision(moment=m) for m in extraction.moments]

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                result = persist_extraction(
                    cur,
                    person=PersonRow(
                        id=person_id,
                        name="Margaret Smith",
                        aliases=["Maggie"],
                    ),
                    extraction=extraction,
                    moment_decisions=decisions,
                    seeded_question_id=None,
                )
    assert result.dropped_entities_count == 1
    assert result.entity_ids == []
