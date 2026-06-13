"""
Fake-cursor tests for told_by provenance stamping in persistence helpers.

These tests call the private insert helpers directly with a fake cursor that
captures SQL + params, so no TEST_DATABASE_URL is required.

Spec (D3/D4):
  - moments:  stamp BOTH told_by_user_id + told_by_display_name on every INSERT.
  - entities: stamp told_by_user_id on fresh INSERT only; NOT on reuse UPDATE.
  - traits:   stamp told_by_user_id on fresh INSERT only; NOT on merge UPDATE.
  - questions (inline P1 dropped_reference): stamp told_by_user_id on INSERT.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from flashback.workers.extraction.persistence import (
    LLMProvenance,
    TraitMergeResolution,
    _insert_dropped_reference_questions,
    _insert_moment,
    _insert_traits,
    _persist_entities,
)
from flashback.workers.extraction.schema import (
    DroppedReference,
    ExtractedEntity,
    ExtractedMoment,
    ExtractedTrait,
)


# ---------------------------------------------------------------------------
# Fake cursor
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Synchronous fake cursor that records (sql, params) for each execute().

    Behaviour by query type:
      - SELECT queries (entity lookup, dropped-phrase dedup check): return None
        from fetchone() so the fresh-insert path fires.
      - INSERT … RETURNING id: return (new_uuid,) from fetchone().
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._next_id: str = str(uuid4())

    def execute(self, sql: str, params=None) -> None:
        self.calls.append((sql, params or ()))

    def fetchone(self):
        last_sql, _ = self.calls[-1]
        if "RETURNING" in last_sql:
            return (self._next_id,)
        # SELECT (lookup / dedup) → None means "not found"
        return None

    def insert_sqls(self) -> list[tuple[str, tuple]]:
        """Return only the INSERT statements captured."""
        return [(sql, params) for sql, params in self.calls if "INSERT" in sql]

    def update_sqls(self) -> list[tuple[str, tuple]]:
        """Return only the UPDATE statements captured."""
        return [(sql, params) for sql, params in self.calls if "UPDATE" in sql]


# ---------------------------------------------------------------------------
# Minimal fixtures
# ---------------------------------------------------------------------------

PERSON_ID = str(uuid4())
USER_ID = str(uuid4())
DISPLAY_NAME = "Priya"

PROV = LLMProvenance(
    provider="anthropic",
    model="claude-sonnet-4-6",
    prompt_version="v1",
)


def _make_moment(**overrides) -> ExtractedMoment:
    base = dict(
        title="A morning walk",
        narrative="Dad walked me to school every day.",
        generation_prompt="A father and child on a leaf-strewn path.",
        involves_entity_indexes=[],
        exemplifies_trait_indexes=[],
    )
    base.update(overrides)
    return ExtractedMoment.model_validate(base)


def _make_entity(**overrides) -> ExtractedEntity:
    base = dict(
        kind="person",
        name="Ravi",
        description="An old friend.",
        generation_prompt="A man in a garden.",
        aliases=[],
        attributes={},
        related_to_entity_indexes=[],
    )
    base.update(overrides)
    return ExtractedEntity.model_validate(base)


def _make_trait(**overrides) -> ExtractedTrait:
    base = dict(name="warmth", description="Warm and generous.")
    base.update(overrides)
    return ExtractedTrait.model_validate(base)


def _make_dropped_ref(**overrides) -> DroppedReference:
    base = dict(
        dropped_phrase="Uncle Dev",
        question_text="Who was Uncle Dev?",
        themes=["family"],
    )
    base.update(overrides)
    return DroppedReference.model_validate(base)


# ---------------------------------------------------------------------------
# _insert_moment
# ---------------------------------------------------------------------------


def test_moment_insert_stamps_told_by():
    """INSERT INTO moments includes told_by_user_id and told_by_display_name."""
    cur = _FakeCursor()
    _insert_moment(
        cur,
        person_id=PERSON_ID,
        moment=_make_moment(),
        llm_provenance=PROV,
        told_by_user_id=USER_ID,
        told_by_display_name=DISPLAY_NAME,
    )
    inserts = cur.insert_sqls()
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "told_by_user_id" in sql
    assert "told_by_display_name" in sql
    assert USER_ID in params
    assert DISPLAY_NAME in params


def test_moment_insert_stamps_null_when_absent():
    """Both told_by columns are None when omitted."""
    cur = _FakeCursor()
    _insert_moment(
        cur,
        person_id=PERSON_ID,
        moment=_make_moment(),
        llm_provenance=PROV,
    )
    inserts = cur.insert_sqls()
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "told_by_user_id" in sql
    assert "told_by_display_name" in sql
    # Both values should be None
    assert params[-2] is None  # told_by_user_id position
    assert params[-1] is None  # told_by_display_name position


# ---------------------------------------------------------------------------
# _persist_entities — fresh insert path
# ---------------------------------------------------------------------------


def test_fresh_entity_insert_stamps_told_by():
    """Fresh entity INSERT includes told_by_user_id."""
    cur = _FakeCursor()
    entities = [_make_entity()]
    _persist_entities(
        cur,
        person_id=PERSON_ID,
        entities=entities,
        llm_provenance=PROV,
        told_by_user_id=USER_ID,
    )
    inserts = cur.insert_sqls()
    # There should be exactly one INSERT INTO entities
    entity_inserts = [(sql, params) for sql, params in inserts if "entities" in sql]
    assert len(entity_inserts) == 1
    sql, params = entity_inserts[0]
    assert "told_by_user_id" in sql
    assert USER_ID in params


# ---------------------------------------------------------------------------
# _persist_entities — reuse path (must NOT restamp)
# ---------------------------------------------------------------------------


class _FakeCursorWithExisting(_FakeCursor):
    """Variant that returns an existing entity row from the SELECT lookup."""

    EXISTING_ID = str(uuid4())

    def fetchone(self):
        last_sql, _ = self.calls[-1]
        if "FROM entities" in last_sql and "lower(btrim(name))" in last_sql:
            # _find_existing_active_entity SELECT
            return (self.EXISTING_ID, "An old description.", [])
        if "RETURNING" in last_sql:
            return (self._next_id,)
        return None

    def fetchall(self):
        return []


def test_entity_reuse_does_not_restamp():
    """The name-match reuse path issues no told_by_user_id write."""
    cur = _FakeCursorWithExisting()
    entities = [_make_entity()]
    results = _persist_entities(
        cur,
        person_id=PERSON_ID,
        entities=entities,
        llm_provenance=PROV,
        told_by_user_id=USER_ID,
    )
    assert results[0].reused is True
    # No INSERT should have been issued (reuse takes the UPDATE path)
    entity_inserts = [(sql, p) for sql, p in cur.insert_sqls() if "entities" in sql]
    assert entity_inserts == []
    # Any UPDATE that was issued should not mention told_by
    for sql, _ in cur.update_sqls():
        assert "told_by" not in sql


# ---------------------------------------------------------------------------
# _insert_traits — fresh insert path
# ---------------------------------------------------------------------------


def test_fresh_trait_insert_stamps_told_by():
    """Fresh trait INSERT includes told_by_user_id (merge_resolution=None)."""
    cur = _FakeCursor()
    traits = [_make_trait()]
    _insert_traits(
        cur,
        person_id=PERSON_ID,
        traits=traits,
        llm_provenance=PROV,
        told_by_user_id=USER_ID,
        merge_resolutions=[None],
    )
    inserts = cur.insert_sqls()
    trait_inserts = [(sql, p) for sql, p in inserts if "traits" in sql]
    assert len(trait_inserts) == 1
    sql, params = trait_inserts[0]
    assert "told_by_user_id" in sql
    assert USER_ID in params


# ---------------------------------------------------------------------------
# _insert_traits — merge UPDATE path (must NOT restamp)
# ---------------------------------------------------------------------------


def test_trait_merge_update_does_not_restamp():
    """The cross-session merge UPDATE branch does not write told_by."""
    existing_trait_id = str(uuid4())
    resolution = TraitMergeResolution(existing_trait_id=existing_trait_id)
    cur = _FakeCursor()
    traits = [_make_trait()]
    _insert_traits(
        cur,
        person_id=PERSON_ID,
        traits=traits,
        llm_provenance=PROV,
        told_by_user_id=USER_ID,
        merge_resolutions=[resolution],
    )
    # No INSERT into traits
    trait_inserts = [(sql, p) for sql, p in cur.insert_sqls() if "traits" in sql]
    assert trait_inserts == []
    # The UPDATE must not contain told_by
    for sql, _ in cur.update_sqls():
        if "traits" in sql:
            assert "told_by" not in sql


# ---------------------------------------------------------------------------
# _insert_dropped_reference_questions
# ---------------------------------------------------------------------------


def test_dropped_reference_question_stamps_told_by():
    """INSERT INTO questions (dropped_reference P1) includes told_by_user_id."""
    cur = _FakeCursor()
    _insert_dropped_reference_questions(
        cur,
        person_id=PERSON_ID,
        dropped_references=[_make_dropped_ref()],
        llm_provenance=PROV,
        told_by_user_id=USER_ID,
    )
    question_inserts = [
        (sql, p) for sql, p in cur.insert_sqls() if "questions" in sql
    ]
    assert len(question_inserts) == 1
    sql, params = question_inserts[0]
    assert "told_by_user_id" in sql
    assert USER_ID in params
