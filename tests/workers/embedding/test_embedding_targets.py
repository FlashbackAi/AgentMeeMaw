"""
Structural test: the EMBEDDING_TARGETS registry must match the schema.

Why this exists: every entry in EMBEDDING_TARGETS encodes a (table,
vector_column) pair the worker writes to. If a future migration
renames either, the worker would silently emit "no such column"
errors at runtime. This test parses 0001_initial_schema.up.sql and
asserts both names exist for every registry entry.

We do *not* validate ``source_column`` for thread/trait because their
source is a SQL expression, not a single column. We validate the
SQL expression compiles by name-checking the columns it references.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from flashback.db.embedding_targets import EMBEDDING_TARGETS

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_FILE = REPO_ROOT / "migrations" / "0001_initial_schema.up.sql"


def _table_columns(sql: str) -> dict[str, set[str]]:
    """
    Naive but adequate parser: pull each ``CREATE TABLE name ( ... )``
    block and extract the leading identifier on each non-blank,
    non-constraint line.
    """
    tables: dict[str, set[str]] = {}
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(\w+)\s*\((.*?)\n\)\s*;",
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(sql):
        name = match.group(1).lower()
        body = match.group(2)
        cols: set[str] = set()
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            upper = line.upper()
            if upper.startswith(("CONSTRAINT", "CHECK", "UNIQUE", "PRIMARY", "FOREIGN")):
                continue
            ident = re.match(r"(\w+)", line)
            if ident:
                cols.add(ident.group(1).lower())
        tables[name] = cols
    return tables


@pytest.fixture(scope="module")
def schema_columns() -> dict[str, set[str]]:
    assert SCHEMA_FILE.exists(), f"missing schema file: {SCHEMA_FILE}"
    return _table_columns(SCHEMA_FILE.read_text(encoding="utf-8"))


def test_registry_covers_five_record_types() -> None:
    assert set(EMBEDDING_TARGETS) == {
        "moment", "entity", "thread", "trait", "question",
    }


def test_every_table_exists_in_schema(schema_columns: dict[str, set[str]]) -> None:
    for record_type, target in EMBEDDING_TARGETS.items():
        assert target.table in schema_columns, (
            f"{record_type} -> table {target.table!r} not found in schema. "
            f"Known tables: {sorted(schema_columns)}"
        )


def test_every_vector_column_exists(schema_columns: dict[str, set[str]]) -> None:
    for record_type, target in EMBEDDING_TARGETS.items():
        cols = schema_columns[target.table]
        assert target.vector_column in cols, (
            f"{record_type}: vector column {target.vector_column!r} "
            f"not found in {target.table}. Columns: {sorted(cols)}"
        )


def test_model_identity_columns_exist(schema_columns: dict[str, set[str]]) -> None:
    """The version-guarded UPDATE writes these three together."""
    for record_type, target in EMBEDDING_TARGETS.items():
        cols = schema_columns[target.table]
        for required in ("embedding_model", "embedding_model_version"):
            assert required in cols, (
                f"{record_type}: {required!r} missing from {target.table}"
            )


def test_simple_source_columns_exist(schema_columns: dict[str, set[str]]) -> None:
    """For moment/entity/question the source is a plain column."""
    plain = {
        "moment": "narrative",
        "entity": "description",
        "question": "text",
    }
    for record_type, column in plain.items():
        target = EMBEDDING_TARGETS[record_type]
        assert column in schema_columns[target.table], (
            f"{record_type}: source column {column!r} not in {target.table}"
        )


def test_thread_expression_references_real_columns(
    schema_columns: dict[str, set[str]],
) -> None:
    cols = schema_columns["threads"]
    assert "name" in cols and "description" in cols


def test_trait_expression_references_real_columns(
    schema_columns: dict[str, set[str]],
) -> None:
    cols = schema_columns["traits"]
    assert "name" in cols and "description" in cols
