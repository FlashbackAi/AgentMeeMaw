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
    db_pool, person_id: str, *, narrative: str, vector: list[float] | None,
    entity_name: str | None = "Family kitchen",
) -> str:
    """Seed a candidate moment. ``vector=None`` leaves it unembedded (the
    embedding worker hasn't caught up); ``entity_name=None`` leaves it with
    no entity links."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            if vector is None:
                cur.execute(
                    """
                    INSERT INTO moments (person_id, title, narrative)
                    VALUES (%s, 'old', %s)
                    RETURNING id::text
                    """,
                    (person_id, narrative),
                )
            else:
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
            if entity_name is not None:
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


def _distant_query(distance: float) -> list[float]:
    """A query vector at the given cosine distance from ``[1, 0, ...]``."""
    sim = 1.0 - distance
    other = (1.0 - sim * sim) ** 0.5
    return [sim, other] + [0.0] * (DIM - 2)


def test_no_entity_new_moment_admitted_when_near_twin(db_pool, make_person):
    """A retell extracted without entity links must still reach the compat
    judge when it is a near-twin (the Swetha prayer-speech duplicate)."""
    person_id = make_person("Ref E")
    moment_id = _seed_existing_moment(
        db_pool,
        person_id,
        narrative="Her first speech at morning prayer.",
        vector=_vec(0.5),
    )
    new_moment = ExtractedMoment(
        title="Speech", narrative="She spoke at the prayer assembly.",
        generation_prompt="speech",
    )
    candidates = find_refinement_candidates(
        new_moment=new_moment,
        new_moment_entity_names=[],  # extraction emitted no links
        person_id=person_id,
        voyage=StubVoyage(vector=_vec(0.5)),  # zero distance
        db_pool=db_pool,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )
    assert [c.id for c in candidates] == [moment_id]


def test_no_entity_new_moment_dropped_between_strict_and_loose(
    db_pool, make_person
):
    """Without entity evidence the strict floor (0.20) applies, not the
    loose vector threshold (0.35)."""
    person_id = make_person("Ref F")
    _seed_existing_moment(
        db_pool,
        person_id,
        narrative="x",
        vector=[1.0] + [0.0] * (DIM - 1),
    )
    new_moment = ExtractedMoment(
        title="x", narrative="y", generation_prompt="z"
    )
    candidates = find_refinement_candidates(
        new_moment=new_moment,
        new_moment_entity_names=[],
        person_id=person_id,
        voyage=StubVoyage(vector=_distant_query(0.28)),
        db_pool=db_pool,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )
    assert candidates == []


def test_candidate_without_entity_edges_admitted_when_near_twin(
    db_pool, make_person
):
    """Mirror case: the OLD moment has no entity links (Swetha's
    first-meeting pair; prod-test-5's AGI basement)."""
    person_id = make_person("Ref G")
    moment_id = _seed_existing_moment(
        db_pool,
        person_id,
        narrative="The AGI basement.",
        vector=_vec(0.5),
        entity_name=None,
    )
    new_moment = ExtractedMoment(
        title="Basement", narrative="Master of the AGI basement.",
        generation_prompt="basement",
    )
    candidates = find_refinement_candidates(
        new_moment=new_moment,
        new_moment_entity_names=["AGI Basement"],
        person_id=person_id,
        voyage=StubVoyage(vector=_vec(0.5)),
        db_pool=db_pool,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )
    assert [c.id for c in candidates] == [moment_id]


def test_recent_unembedded_moment_is_compared_query_side(db_pool, make_person):
    """A moment the embedding worker hasn't reached yet is invisible to the
    vector search but must still be considered — retells cluster within
    minutes of the original (prod-test-5: twin 36s later, embedding 12min
    later)."""
    person_id = make_person("Ref H")
    moment_id = _seed_existing_moment(
        db_pool,
        person_id,
        narrative="They talked about the kitchen.",
        vector=None,  # not yet embedded
    )
    new_moment = ExtractedMoment(
        title="Kitchen", narrative="They were in the kitchen.",
        generation_prompt="kitchen",
    )
    voyage = StubVoyage(vector=_vec(0.5))  # identical vectors -> distance 0
    candidates = find_refinement_candidates(
        new_moment=new_moment,
        new_moment_entity_names=["Family kitchen"],
        person_id=person_id,
        voyage=voyage,
        db_pool=db_pool,
        embedding_model=MODEL,
        embedding_model_version=VERSION,
    )
    assert [c.id for c in candidates] == [moment_id]
    # The candidate's narrative was embedded query-side.
    assert "They talked about the kitchen." in voyage.calls


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
