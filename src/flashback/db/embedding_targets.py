"""
Registry of embedded record types.

Every row that carries an embedding lives in one of five tables:
``moments``, ``entities``, ``threads``, ``traits``, ``questions``.
Each table has:

    * a source field (or expression) - the text we hand to Voyage
    * a vector column - where the resulting 1024-dim vector lands
    * the standard pair of model identity columns
      (``embedding_model``, ``embedding_model_version``) which the
      embedding worker writes in lockstep with the vector

This registry is the single source of truth for the worker and the
backfill CLI. The structural test in
``tests/workers/embedding/test_embedding_targets.py`` parses
``migrations/0001_initial_schema.up.sql`` and asserts that every
table and vector column declared here actually exists, so the
registry cannot drift from the schema unnoticed.

Reference: ARCHITECTURE.md s6.1, QUESTION_BANK.md s6, SCHEMA.md s6.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingTarget:
    record_type: str
    table: str
    source_column: str
    vector_column: str
    source_sql_expr: str


EMBEDDING_TARGETS: dict[str, EmbeddingTarget] = {
    "moment": EmbeddingTarget(
        record_type="moment",
        table="moments",
        source_column="narrative",
        vector_column="narrative_embedding",
        source_sql_expr="narrative",
    ),
    "entity": EmbeddingTarget(
        record_type="entity",
        table="entities",
        source_column="description",
        vector_column="description_embedding",
        source_sql_expr="description",
    ),
    "thread": EmbeddingTarget(
        record_type="thread",
        table="threads",
        source_column="name+description",
        vector_column="description_embedding",
        source_sql_expr="name || ', ' || description",
    ),
    "trait": EmbeddingTarget(
        record_type="trait",
        table="traits",
        source_column="name+description",
        vector_column="description_embedding",
        source_sql_expr="name || COALESCE(', ' || description, '')",
    ),
    "question": EmbeddingTarget(
        record_type="question",
        table="questions",
        source_column="text",
        vector_column="embedding",
        source_sql_expr="text",
    ),
}


def get_target(record_type: str) -> EmbeddingTarget:
    try:
        return EMBEDDING_TARGETS[record_type]
    except KeyError as exc:
        raise ValueError(
            f"Unknown record_type {record_type!r}. "
            f"Known: {sorted(EMBEDDING_TARGETS)}"
        ) from exc
