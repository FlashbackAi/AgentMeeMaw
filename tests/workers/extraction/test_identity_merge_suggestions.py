from __future__ import annotations

from flashback.workers.extraction.persistence import (
    MomentDecision,
    PersonRow,
    persist_extraction,
)
from flashback.workers.extraction.schema import ExtractionResult


def test_extraction_alias_match_creates_pending_merge_suggestion(
    db_pool, make_person
):
    person_id = make_person("Test Subject")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entities
                      (person_id, kind, name, description, aliases)
                VALUES (%s, 'person', %s, %s, '{}')
                RETURNING id::text
                """,
                (
                    person_id,
                    "Earlier label",
                    "A person initially identified by an earlier label.",
                ),
            )
            source_id = cur.fetchone()[0]
            conn.commit()

    extraction = ExtractionResult.model_validate(
        {
            "moments": [],
            "entities": [
                {
                    "kind": "person",
                    "name": "Person B",
                    "description": (
                        "Person B, clarified by the contributor as the person "
                        "previously called the earlier label."
                    ),
                    "aliases": ["Earlier label"],
                    "attributes": {"relationship": "partner"},
                    "related_to_entity_indexes": [],
                    "generation_prompt": "Two close friends at a farmhouse party.",
                }
            ],
            "traits": [],
            "dropped_references": [],
            "extraction_notes": "Identity correction from old label to canonical name.",
        }
    )

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                result = persist_extraction(
                    cur,
                    person=PersonRow(id=person_id, name="Test Subject", aliases=[]),
                    extraction=extraction,
                    moment_decisions=[],
                    seeded_question_id=None,
                )

    assert len(result.merge_suggestion_ids) == 1

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_entity_id::text, target_entity_id::text,
                       proposed_alias, status
                  FROM identity_merge_suggestions
                 WHERE id = %s
                """,
                (result.merge_suggestion_ids[0],),
            )
            suggestion = cur.fetchone()

    assert suggestion[0] == source_id
    assert suggestion[1] == result.entity_ids[0]
    assert suggestion[2] == "Earlier label"
    assert suggestion[3] == "pending"


def test_extraction_description_match_creates_pending_merge_suggestion(
    db_pool, make_person
):
    person_id = make_person("Test Subject")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entities
                      (person_id, kind, name, description, aliases)
                VALUES (%s, 'person', %s, %s, '{}')
                RETURNING id::text
                """,
                (
                    person_id,
                    "Earlier label",
                    "A person initially identified by an earlier label.",
                ),
            )
            source_id = cur.fetchone()[0]
            conn.commit()

    extraction = ExtractionResult.model_validate(
        {
            "moments": [],
            "entities": [
                {
                    "kind": "person",
                    "name": "Person B",
                    "description": "Earlier label for several years.",
                    "aliases": [],
                    "attributes": {"relationship": "partner"},
                    "related_to_entity_indexes": [],
                    "generation_prompt": "Two people outside a farmhouse party.",
                }
            ],
            "traits": [],
            "dropped_references": [],
            "extraction_notes": "Identity correction from description only.",
        }
    )

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                result = persist_extraction(
                    cur,
                    person=PersonRow(id=person_id, name="Test Subject", aliases=[]),
                    extraction=extraction,
                    moment_decisions=[],
                    seeded_question_id=None,
                )

    assert len(result.merge_suggestion_ids) == 1

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_entity_id::text, target_entity_id::text,
                       proposed_alias, status
                  FROM identity_merge_suggestions
                 WHERE id = %s
                """,
                (result.merge_suggestion_ids[0],),
            )
            suggestion = cur.fetchone()

    assert suggestion == (
        source_id,
        result.entity_ids[0],
        "Earlier label",
        "pending",
    )


def test_extraction_same_name_duplicate_creates_pending_merge_suggestion(
    db_pool, make_person
):
    person_id = make_person("Test Subject")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entities
                      (person_id, kind, name, description, aliases)
                VALUES (%s, 'person', 'Person B', 'A friend from training.', '{}')
                RETURNING id::text
                """,
                (person_id,),
            )
            source_id = cur.fetchone()[0]
            conn.commit()

    extraction = ExtractionResult.model_validate(
        {
            "moments": [],
            "entities": [
                {
                    "kind": "person",
                    "name": "Person B",
                    "description": "The subject's partner and event friend.",
                    "aliases": [],
                    "attributes": {"relationship": "partner"},
                    "related_to_entity_indexes": [],
                    "generation_prompt": "Friends in a shared vehicle.",
                }
            ],
            "traits": [],
            "dropped_references": [],
            "extraction_notes": "A duplicate person mention.",
        }
    )

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                result = persist_extraction(
                    cur,
                    person=PersonRow(id=person_id, name="Test Subject", aliases=[]),
                    extraction=extraction,
                    moment_decisions=[],
                    seeded_question_id=None,
                )

    assert len(result.merge_suggestion_ids) == 1

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_entity_id::text, target_entity_id::text,
                       proposed_alias, status
                  FROM identity_merge_suggestions
                 WHERE id = %s
                """,
                (result.merge_suggestion_ids[0],),
            )
            suggestion = cur.fetchone()

    assert suggestion == (source_id, result.entity_ids[0], "Person B", "pending")
