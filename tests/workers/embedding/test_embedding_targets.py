"""
Structural test: the EMBEDDING_TARGETS registry must match the schema.

Why this exists: every entry in EMBEDDING_TARGETS encodes a (table,
vector_column) pair the worker writes to. If a future migration
renames either, the worker would silently emit "no such column"
errors at runtime. This test parses every ``migrations/*.up.sql`` and
asserts both names exist for every registry entry.

It parses the whole migration set, not just 0001 — tables added later
(``profile_facts``, migration 0010) are just as much part of the
registry's contract as the initial ones.

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
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def _merge_table_columns(sql: str, tables: dict[str, set[str]]) -> None:
    """
    Naive but adequate parser, folded into ``tables``: pull each
    ``CREATE TABLE name ( ... )`` block and extract the leading identifier
    on each non-blank, non-constraint line, then apply any
    ``ALTER TABLE name ADD COLUMN col`` so columns introduced by a later
    migration are visible too.
    """
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\n\)\s*;",
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(sql):
        name = match.group(1).lower()
        body = match.group(2)
        cols = tables.setdefault(name, set())
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

    alter = re.compile(
        r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)\s+"
        r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
        re.IGNORECASE,
    )
    for match in alter.finditer(sql):
        tables.setdefault(match.group(1).lower(), set()).add(match.group(2).lower())


@pytest.fixture(scope="module")
def schema_columns() -> dict[str, set[str]]:
    up_files = sorted(MIGRATIONS_DIR.glob("*.up.sql"))
    assert up_files, f"no migrations found under {MIGRATIONS_DIR}"
    tables: dict[str, set[str]] = {}
    for path in up_files:
        _merge_table_columns(path.read_text(encoding="utf-8"), tables)
    return tables


def test_registry_covers_expected_record_types() -> None:
    assert set(EMBEDDING_TARGETS) == {
        "moment", "entity", "thread", "trait", "question", "profile_fact",
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
