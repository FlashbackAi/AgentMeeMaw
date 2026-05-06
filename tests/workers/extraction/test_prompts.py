"""
Drift-detector + JSON-Schema validity tests for the extraction prompts.

If migration 0001 grows a new entity ``kind`` (or removes one), the
matching test here fails until ``ENTITY_KINDS`` in
:mod:`flashback.workers.extraction.prompts` is updated.
"""

from __future__ import annotations

import re
from pathlib import Path

import jsonschema
import pytest

from flashback.workers.extraction.prompts import (
    COMPATIBILITY_TOOL,
    ENTITY_KINDS,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_TOOL,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
INITIAL_MIGRATION = REPO_ROOT / "migrations" / "0001_initial_schema.up.sql"


def test_extraction_tool_input_schema_is_valid_json_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(EXTRACTION_TOOL.input_schema)


def test_compatibility_tool_input_schema_is_valid_json_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(COMPATIBILITY_TOOL.input_schema)


def test_entity_kinds_match_migration_check_constraint() -> None:
    """The ``kind`` enum in the tool must match the migration's CHECK list."""
    sql = INITIAL_MIGRATION.read_text(encoding="utf-8")
    # Match the entity kinds CHECK clause: kind IN ('person','place','object','organization')
    match = re.search(
        r"kind\s+TEXT\s+NOT\s+NULL\s*\n?\s*CHECK\s*\(\s*kind\s+IN\s*\(([^)]+)\)\)",
        sql,
        re.IGNORECASE,
    )
    assert match, "could not locate entities.kind CHECK constraint in migration"
    raw = match.group(1)
    migration_kinds = tuple(
        token.strip().strip("'\"") for token in raw.split(",")
    )
    assert tuple(ENTITY_KINDS) == migration_kinds, (
        f"ENTITY_KINDS drift: prompts={ENTITY_KINDS} vs migration={migration_kinds}"
    )


def test_entity_kinds_in_tool_schema_match_constant() -> None:
    """The enum baked into the tool input_schema mirrors ENTITY_KINDS."""
    schema_kinds = tuple(
        EXTRACTION_TOOL.input_schema["properties"]["entities"]["items"][
            "properties"
        ]["kind"]["enum"]
    )
    assert schema_kinds == ENTITY_KINDS


def test_dropped_references_themes_min_items_is_one() -> None:
    """Invariant #9: every dropped_reference question carries a non-empty themes list."""
    items = EXTRACTION_TOOL.input_schema["properties"]["dropped_references"][
        "items"
    ]
    themes = items["properties"]["themes"]
    assert themes["minItems"] == 1
    assert "themes" in items["required"]


def test_extraction_tool_max_three_moments_three_dropped_references() -> None:
    moments = EXTRACTION_TOOL.input_schema["properties"]["moments"]
    drs = EXTRACTION_TOOL.input_schema["properties"]["dropped_references"]
    assert moments["maxItems"] == 3
    assert drs["maxItems"] == 3


def test_compatibility_verdict_enum() -> None:
    enum = COMPATIBILITY_TOOL.input_schema["properties"]["verdict"]["enum"]
    assert sorted(enum) == ["contradiction", "independent", "refinement"]


def test_extraction_prompt_preserves_actor_attribution() -> None:
    assert "Preserve actor attribution" in EXTRACTION_SYSTEM_PROMPT
    assert "CLOSED SEGMENT is the source of truth" in EXTRACTION_SYSTEM_PROMPT
    assert "do not transfer" in EXTRACTION_SYSTEM_PROMPT
