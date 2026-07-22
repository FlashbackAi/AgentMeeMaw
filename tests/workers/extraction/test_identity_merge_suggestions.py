"""Prevention layer 1: deterministic entity reuse at persistence.

Replaces the old brute-force "extraction creates a pending merge
suggestion" tests. The inline string-match suggestion path was removed
(2026-06-06 redesign); same-name duplicates are now collapsed at insert
time, and the verifier-gated reconcile handles the rest out of band.
"""

from __future__ import annotations

from psycopg.types.json import Json

from flashback.workers.extraction.persistence import (
    PersonRow,
    persist_extraction,
)
from flashback.workers.extraction.schema import ExtractionResult


def _extraction_with_entity(
    name: str, *, description: str, aliases=None, gender: str | None = None
) -> ExtractionResult:
    attributes: dict = {"relationship": "friend"}
    if gender is not None:
        attributes["gender"] = gender
    return ExtractionResult.model_validate(
        {
            "moments": [],
            "entities": [
                {
                    "kind": "person",
                    "name": name,
                    "description": description,
                    "aliases": list(aliases or []),
                    "attributes": attributes,
                    "related_to_entity_indexes": [],
                    "generation_prompt": "A friend at a farmhouse party.",
                }
            ],
            "traits": [],
            "dropped_references": [],
            "extraction_notes": "test",
        }
    )


def _insert_entity(
    db_pool, person_id, name, description="An existing person.", attributes=None
):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            if attributes is None:
                cur.execute(
                    """
                    INSERT INTO entities (person_id, kind, name, description, aliases)
                    VALUES (%s, 'person', %s, %s, '{}')
                    RETURNING id::text
                    """,
                    (person_id, name, description),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO entities
                          (person_id, kind, name, description, aliases, attributes)
                    VALUES (%s, 'person', %s, %s, '{}', %s)
                    RETURNING id::text
                    """,
                    (person_id, name, description, Json(attributes)),
                )
            entity_id = cur.fetchone()[0]
            conn.commit()
    return entity_id


def _get_attributes(db_pool, entity_id) -> dict:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT attributes FROM entities WHERE id = %s", (entity_id,))
            (attributes,) = cur.fetchone()
    return dict(attributes or {})


def _count_active(db_pool, person_id, name):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM entities
                 WHERE person_id = %s AND status = 'active'
                   AND lower(btrim(name)) = lower(btrim(%s))
                """,
                (person_id, name),
            )
            return cur.fetchone()[0]


def test_same_name_entity_is_reused_not_duplicated(db_pool, make_person):
    """A re-mentioned same-name entity reuses the existing row — no
    duplicate, no artifact, no merge suggestion."""
    person_id = make_person("Test Subject")
    existing_id = _insert_entity(db_pool, person_id, "Aarav")

    extraction = _extraction_with_entity("Aarav", description="A close friend.")

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

    assert result.entity_ids == [existing_id]
    assert result.entity_writes[0].reused is True
    assert result.merge_suggestion_ids == []
    assert _count_active(db_pool, person_id, "Aarav") == 1


def test_same_name_reuse_folds_aliases_and_fills_empty_description(
    db_pool, make_person
):
    person_id = make_person("Test Subject")
    # Existing row has an empty description.
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entities (person_id, kind, name, description, aliases)
                VALUES (%s, 'person', 'Ishita', NULL, ARRAY['Ish'])
                RETURNING id::text
                """,
                (person_id,),
            )
            existing_id = cur.fetchone()[0]
            conn.commit()

    extraction = _extraction_with_entity(
        "Ishita", description="The subject's mother.", aliases=["Mom"]
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

    assert result.entity_ids == [existing_id]
    write = result.entity_writes[0]
    assert write.reused is True
    assert write.description_changed is True
    assert write.embed_text == "The subject's mother."

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT description, aliases FROM entities WHERE id = %s",
                (existing_id,),
            )
            description, aliases = cur.fetchone()
    assert description == "The subject's mother."
    assert set(aliases) == {"Ish", "Mom"}


def test_reuse_applies_description_override_and_reembeds(db_pool, make_person):
    """A pre-computed blended description (entity_description_overrides)
    overwrites the reused row's description and signals a re-embed."""
    person_id = make_person("Test Subject")
    existing_id = _insert_entity(db_pool, person_id, "Aarav", description="A friend.")

    extraction = _extraction_with_entity("Aarav", description="A childhood neighbour.")
    blended = "A childhood friend and neighbour."

    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                result = persist_extraction(
                    cur,
                    person=PersonRow(id=person_id, name="Test Subject", aliases=[]),
                    extraction=extraction,
                    moment_decisions=[],
                    seeded_question_id=None,
                    entity_description_overrides={("person", "aarav"): blended},
                )

    assert result.entity_ids == [existing_id]
    write = result.entity_writes[0]
    assert write.reused is True
    assert write.description_changed is True
    assert write.embed_text == blended

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT description FROM entities WHERE id = %s", (existing_id,))
            assert cur.fetchone()[0] == blended


def test_different_name_creates_new_entity(db_pool, make_person):
    person_id = make_person("Test Subject")
    _insert_entity(db_pool, person_id, "Aarav")

    extraction = _extraction_with_entity("Comet", description="The family dog.")

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

    assert result.entity_writes[0].reused is False
    assert _count_active(db_pool, person_id, "Comet") == 1


def test_different_kind_same_name_is_not_reused(db_pool, make_person):
    """A place named 'Comet' must not reuse an object named 'Comet'."""
    person_id = make_person("Test Subject")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entities (person_id, kind, name, description, aliases)
                VALUES (%s, 'object', 'Comet', 'A bicycle.', '{}')
                """,
                (person_id,),
            )
            conn.commit()

    extraction = ExtractionResult.model_validate(
        {
            "moments": [],
            "entities": [
                {
                    "kind": "place",
                    "name": "Comet",
                    "description": "A diner downtown.",
                    "aliases": [],
                    "attributes": {},
                    "related_to_entity_indexes": [],
                    "generation_prompt": "A diner at dusk.",
                }
            ],
            "traits": [],
            "dropped_references": [],
            "extraction_notes": "test",
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

    # New place row created; the object row is untouched.
    assert result.entity_writes[0].reused is False
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT kind FROM entities
                 WHERE person_id = %s AND status='active' AND name='Comet'
                 ORDER BY kind
                """,
                (person_id,),
            )
            kinds = [r[0] for r in cur.fetchall()]
    assert kinds == ["object", "place"]


def test_reuse_fills_empty_gender_from_extracted_attributes(db_pool, make_person):
    """Deterministic reuse (invariant #17a): a newly-known ``attributes.gender``
    folds into an existing entity only when the stored value is empty."""
    person_id = make_person("Test Subject")
    existing_id = _insert_entity(db_pool, person_id, "Aarav")  # attributes = {}

    extraction = _extraction_with_entity(
        "Aarav", description="A close friend.", gender="male"
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

    assert result.entity_ids == [existing_id]
    assert result.entity_writes[0].reused is True
    assert _get_attributes(db_pool, existing_id).get("gender") == "male"


def test_reuse_never_overwrites_an_already_set_gender(db_pool, make_person):
    """A later mention with a different/ambiguous gender must never clobber
    a confidently-stored one (invariant #17a)."""
    person_id = make_person("Test Subject")
    existing_id = _insert_entity(
        db_pool, person_id, "Aarav", attributes={"gender": "male"}
    )

    extraction = _extraction_with_entity(
        "Aarav", description="A close friend.", gender="female"
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

    assert result.entity_ids == [existing_id]
    assert result.entity_writes[0].reused is True
    assert _get_attributes(db_pool, existing_id).get("gender") == "male"
