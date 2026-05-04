"""Prompt and tool-schema guard tests."""

from __future__ import annotations

from jsonschema import Draft7Validator

from flashback.workers.producers.prompts import P2_TOOL, P3_TOOL, P5_TOOL
from flashback.workers.producers.universal import (
    UNIVERSAL_DIMENSION_KEYWORDS,
    UNIVERSAL_DIMENSIONS,
)


def test_tool_schemas_are_valid_json_schema() -> None:
    for tool in (P2_TOOL, P3_TOOL, P5_TOOL):
        Draft7Validator.check_schema(tool.input_schema)


def test_tool_required_fields() -> None:
    p2_item = P2_TOOL.input_schema["properties"]["questions"]["items"]
    p3_item = P3_TOOL.input_schema["properties"]["questions"]["items"]
    p5_item = P5_TOOL.input_schema["properties"]["questions"]["items"]

    assert p2_item["required"] == ["text", "targets_entity_id", "themes"]
    assert p3_item["required"] == ["text", "life_period", "themes"]
    assert p5_item["required"] == ["text", "dimension", "themes"]


def test_themes_min_items_on_all_tools() -> None:
    for tool in (P2_TOOL, P3_TOOL, P5_TOOL):
        item = tool.input_schema["properties"]["questions"]["items"]
        assert item["properties"]["themes"]["minItems"] == 1


def test_universal_dimensions_and_keywords_do_not_drift() -> None:
    assert set(UNIVERSAL_DIMENSIONS) == set(UNIVERSAL_DIMENSION_KEYWORDS)

