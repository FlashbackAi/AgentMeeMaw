"""Theme-aware extraction surfaces: schema + tool-schema drift."""

from __future__ import annotations

import jsonschema

from flashback.workers.extraction.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_TOOL,
)
from flashback.workers.extraction.schema import ExtractedMoment


def test_extracted_moment_accepts_themes_list() -> None:
    m = ExtractedMoment(
        title="Wedding day",
        narrative="They got married at a small lakeside venue.",
        generation_prompt="A small lakeside chapel at golden hour.",
        themes=["family", "milestones"],
    )
    assert m.themes == ["family", "milestones"]


def test_extracted_moment_themes_default_empty() -> None:
    m = ExtractedMoment(
        title="Quiet evening",
        narrative="An evening reading by the window.",
        generation_prompt="A reading lamp lighting an open book.",
    )
    assert m.themes == []


def test_extraction_tool_schema_includes_themes_in_moment() -> None:
    moment_props = EXTRACTION_TOOL.input_schema["properties"]["moments"]["items"][
        "properties"
    ]
    assert "themes" in moment_props
    assert moment_props["themes"]["type"] == "array"
    assert moment_props["themes"]["items"]["type"] == "string"


def test_extraction_tool_themes_not_required_on_moments() -> None:
    """Theme tagging is optional — moments without any tags are valid."""
    required = EXTRACTION_TOOL.input_schema["properties"]["moments"]["items"][
        "required"
    ]
    assert "themes" not in required


def test_extraction_tool_schema_is_still_valid_json_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(EXTRACTION_TOOL.input_schema)


def test_extraction_system_prompt_mentions_themes() -> None:
    assert "<theme_catalog>" in EXTRACTION_SYSTEM_PROMPT
    assert "Multi-tag" in EXTRACTION_SYSTEM_PROMPT
