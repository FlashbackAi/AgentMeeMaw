"""
Refinement candidate search tests.

We pre-write a candidate moment with an explicit narrative_embedding so
the vector search can find it. The Voyage stub returns a vector close to
the stored one (cosine distance below the default 0.35 threshold).
"""

from __future__ import annotations

import pytest

from flashback.workers.extraction.refinement import (
    collect_entity_names_for_moment,
    find_refinement_candidates,
)
from flashback.workers.extraction.schema import (
    ExtractedEntity,
    ExtractedMoment,
    ExtractionResult,
)
from tests.workers.extraction.conftest import StubVoyage

MODEL = "voyage-3-large"
VERSION = "2025-01-07"
DIM = 1024


def _vec(value: float) -> list[float]:
    return [value] * DIM


def _seed_existing_moment(
    db_pool, person_id: str, *, narrative: str, vector: list[float],
    entity_name: str = "Family kitchen",
) -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO moments
                      (person_id, title, narrative,
                       narrative_embedding, embedding_model, embedding_model_version)
                VALUES (%s, 'old', %s, %s::vector, %s, %s)
                RETURNING id::text
                """,
                (person_id, narrative, vector, MODEL, VERSION),
            )
            moment_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO entities (person_id, kind, name)
                VALUES (%s, 'place', %s)
                RETURNING id::text
                """,
                (person_id, entity_name),
            )
            entity_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO edges (from_kind, from_id, to_kind, to_id, edge_type)
                VALUES ('moment', %s, 'entity', %s, 'involves')
                """,
                (moment_id, entity_id),
            )
            conn.commit()
    return moment_id


def test_vector_search_finds_candidate_within_threshold(db_pool, make_person):
    person_id = make_person("Ref A")
    seed_vec = _vec(0.5)
    moment_id = _seed_existing_moment(
        db_pool,
        person_id,
        narrative="They talked about the kitchen.",
        vector=seed_vec,
    )

    new_moment = ExtractedMoment(
        title="Kitchen",
        narrative="They were in the kitchen.",
        generation_prompt="kitchen",
    )

    voyage = StubVoyage(vector=seed_vec)  # zero distance
    candidates = find_refinement_candidates(
        new_moment=new_moment,
        new_moment_entity_names=["Family kitchen"],
        person_id=person_id,
        voyage=voyage,
        db_pool=db_pool,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
        distance_threshold=0.35,
    )
    assert len(candidates) == 1
    assert candidates[0].id == moment_id


def test_entity_overlap_filter_drops_candidates_without_shared_names(
    db_pool, make_person
):
    person_id = make_person("Ref B")
    _seed_existing_moment(
        db_pool,
        person_id,
        narrative="They talked about the porch.",
        vector=_vec(0.5),
        entity_name="Front porch",
    )

    new_moment = ExtractedMoment(
        title="Different",
        narrative="They were in the kitchen.",
        generation_prompt="kitchen",
    )
    voyage = StubVoyage(vector=_vec(0.5))
    candidates = find_refinement_candidates(
        new_moment=new_moment,
        new_moment_entity_names=["Garage"],  # no overlap
        person_id=person_id,
        voyage=voyage,
        db_pool=db_pool,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )
    assert candidates == []


def test_voyage_failure_returns_empty(db_pool, make_person):
    person_id = make_person("Ref C")
    _seed_existing_moment(
        db_pool,
        person_id,
        narrative="x",
        vector=_vec(0.5),
        entity_name="House",
    )
    new_moment = ExtractedMoment(
        title="x", narrative="y", generation_prompt="z"
    )
    voyage = StubVoyage(return_none=True)
    candidates = find_refinement_candidates(
        new_moment=new_moment,
        new_moment_entity_names=["House"],
        person_id=person_id,
        voyage=voyage,
        db_pool=db_pool,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )
    assert candidates == []


def test_far_vector_returns_no_candidates(db_pool, make_person):
    person_id = make_person("Ref D")
    _seed_existing_moment(
        db_pool,
        person_id,
        narrative="x",
        vector=[1.0] + [0.0] * (DIM - 1),
        entity_name="House",
    )
    new_moment = ExtractedMoment(
        title="x", narrative="y", generation_prompt="z"
    )
    far_vec = [-1.0] + [0.0] * (DIM - 1)  # cosine distance ≈ 2 (opposite)
    voyage = StubVoyage(vector=far_vec)
    candidates = find_refinement_candidates(
        new_moment=new_moment,
        new_moment_entity_names=["House"],
        person_id=person_id,
        voyage=voyage,
        db_pool=db_pool,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )
    assert candidates == []


def test_collect_entity_names_for_moment_resolves_indexes() -> None:
    payload = {
        "moments": [
            {
                "title": "x",
                "narrative": "y",
                "generation_prompt": "z",
                "involves_entity_indexes": [0, 1],
                "happened_at_entity_index": 1,
            }
        ],
        "entities": [
            {"kind": "person", "name": "Dad", "generation_prompt": "p"},
            {"kind": "place", "name": "Kitchen", "generation_prompt": "p"},
        ],
        "traits": [],
        "dropped_references": [],
        "extraction_notes": "",
    }
    extraction = ExtractionResult.model_validate(payload)
    names = collect_entity_names_for_moment(extraction, extraction.moments[0])
    assert "Dad" in names
    assert "Kitchen" in names
