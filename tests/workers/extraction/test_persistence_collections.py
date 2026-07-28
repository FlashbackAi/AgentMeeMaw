"""Extraction persistence writes moments.storybook_collections, filtering
unknown slugs (design 2026-07-06)."""

from __future__ import annotations

from flashback.workers.extraction.persistence import (
    MomentDecision,
    PersonRow,
    persist_extraction,
)
from flashback.workers.extraction.schema import ExtractionResult


def _one_moment(collections: list[str]) -> ExtractionResult:
    return ExtractionResult.model_validate(
        {
            "moments": [
                {
                    "title": "The pond at dawn",
                    "narrative": "n",
                    "generation_prompt": "a pond at dawn",
                    "sensory_details": "cold mist",
                    "collections": collections,
                }
            ],
            "entities": [],
            "traits": [],
            "dropped_references": [],
            "extraction_notes": "",
        }
    )


def _stored_collections(db_pool, person_id: str) -> list[str]:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT storybook_collections FROM moments "
                "WHERE person_id = %s",
                (person_id,),
            )
            return cur.fetchone()[0]


def test_valid_and_unknown_slugs_filtered(db_pool, make_person):
    person_id = make_person("Dad")
    extraction = _one_moment(["childhood", "memoir", "festivals", "childhood"])
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                persist_extraction(
                    cur,
                    person=PersonRow(id=person_id, name="Dad", aliases=[]),
                    extraction=extraction,
                    moment_decisions=[
                        MomentDecision(moment=m) for m in extraction.moments
                    ],
                )
    # memoir dropped, dupe collapsed, order preserved.
    assert _stored_collections(db_pool, person_id) == ["childhood", "festivals"]


def test_no_tags_writes_empty_array_not_null(db_pool, make_person):
    person_id = make_person("Mum")
    extraction = _one_moment([])
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                persist_extraction(
                    cur,
                    person=PersonRow(id=person_id, name="Mum", aliases=[]),
                    extraction=extraction,
                    moment_decisions=[
                        MomentDecision(moment=m) for m in extraction.moments
                    ],
                )
    # '{}' (tagged, fits nothing) — distinct from NULL (never tagged).
    assert _stored_collections(db_pool, person_id) == []
