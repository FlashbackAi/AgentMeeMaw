"""
Supersession test: a refinement match marks the old moment superseded,
repoints inbound edges, and deletes outbound edges from the old moment.
"""

from __future__ import annotations

from flashback.workers.extraction.persistence import (
    MomentDecision,
    PersonRow,
    persist_extraction,
)
from flashback.workers.extraction.schema import ExtractionResult
from tests.workers.extraction.fixtures import sample_extractions


def _insert_existing_moment(db_pool, person_id: str) -> tuple[str, str, str]:
    """Insert an existing active moment, an entity, and a question with
    inbound + outbound edges. Returns the (moment_id, question_id, entity_id)."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments (person_id, title, narrative)
                VALUES (%s, 'old', 'older retelling')
                RETURNING id::text
                """,
                (person_id,),
            )
            moment_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO entities (person_id, kind, name)
                VALUES (%s, 'place', 'Old kitchen')
                RETURNING id::text
                """,
                (person_id,),
            )
            entity_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO questions (person_id, text, source, attributes)
                VALUES (%s, 'q', 'underdeveloped_entity',
                        '{"themes":["x"]}'::jsonb)
                RETURNING id::text
                """,
                (person_id,),
            )
            question_id = cur.fetchone()[0]

            # Inbound edge: question --answered_by--> moment (old)
            cur.execute(
                """
                INSERT INTO edges (from_kind, from_id, to_kind, to_id, edge_type)
                VALUES ('question', %s, 'moment', %s, 'answered_by')
                """,
                (question_id, moment_id),
            )

            # Outbound edge: moment (old) --involves--> entity
            cur.execute(
                """
                INSERT INTO edges (from_kind, from_id, to_kind, to_id, edge_type)
                VALUES ('moment', %s, 'entity', %s, 'involves')
                """,
                (moment_id, entity_id),
            )
            conn.commit()
    return moment_id, question_id, entity_id


def test_supersession_marks_old_repoints_inbound_deletes_outbound(
    db_pool, make_person
):
    person_id = make_person("Sup Subject")
    old_moment_id, question_id, _entity_id = _insert_existing_moment(
        db_pool, person_id
    )

    extraction = ExtractionResult.model_validate(
        sample_extractions.clean_extraction()
    )
    decisions = [MomentDecision(moment=m) for m in extraction.moments]
    decisions[0].supersedes_id = old_moment_id

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                result = persist_extraction(
                    cur,
                    person=PersonRow(id=person_id, name="Sup Subject", aliases=[]),
                    extraction=extraction,
                    moment_decisions=decisions,
                    seeded_question_id=None,
                )

    new_moment_id = result.moment_ids[0]
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            # 1. Old marked superseded with FK to new.
            cur.execute(
                "SELECT status, superseded_by::text FROM moments WHERE id=%s",
                (old_moment_id,),
            )
            status, superseded_by = cur.fetchone()
            assert status == "superseded"
            assert superseded_by == new_moment_id

            # 2. Inbound edge repointed.
            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE from_kind='question' AND from_id=%s
                   AND to_kind='moment'    AND to_id=%s
                   AND edge_type='answered_by'
                """,
                (question_id, new_moment_id),
            )
            assert cur.fetchone()[0] == 1

            # No remaining inbound edges to the old moment.
            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE to_kind='moment' AND to_id=%s
                """,
                (old_moment_id,),
            )
            assert cur.fetchone()[0] == 0

            # 3. Outbound edges from old moment are gone.
            cur.execute(
                """
                SELECT count(*) FROM edges
                 WHERE from_kind='moment' AND from_id=%s
                """,
                (old_moment_id,),
            )
            assert cur.fetchone()[0] == 0
