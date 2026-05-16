"""Emergent-theme fields on NamingResult + the naming tool schema."""

from __future__ import annotations

import jsonschema

from flashback.workers.thread_detector.prompts import NAMING_TOOL
from flashback.workers.thread_detector.schema import NamingResult


def test_naming_tool_schema_is_valid_json_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(NAMING_TOOL.input_schema)


def test_naming_tool_includes_theme_fields() -> None:
    props = NAMING_TOOL.input_schema["properties"]
    assert "theme_display_name" in props
    assert "theme_slug" in props
    assert "theme_description" in props
    # Theme fields are OPTIONAL — required only contains coherent + reasoning
    assert "theme_display_name" not in NAMING_TOOL.input_schema["required"]
    assert "theme_slug" not in NAMING_TOOL.input_schema["required"]


def test_naming_result_has_emergent_theme_true_when_all_fields_set() -> None:
    result = NamingResult(
        coherent=True,
        reasoning="cluster about cricket years",
        name="Cricket years",
        description="Years spent obsessed with cricket.",
        generation_prompt="A wooden cricket bat catching morning light.",
        theme_display_name="Love of cricket",
        theme_slug="love_of_cricket",
        theme_description="Cricket as a thread through this life.",
    )
    assert result.has_emergent_theme() is True


def test_naming_result_has_emergent_theme_false_when_any_field_missing() -> None:
    base = NamingResult(
        coherent=True,
        reasoning="generic family stories",
        name="Family stories",
        description="Family stories.",
        generation_prompt="A warm dinner scene.",
    )
    assert base.has_emergent_theme() is False

    partial = base.model_copy(update={"theme_display_name": "Love of cricket"})
    assert partial.has_emergent_theme() is False  # slug + description missing


def test_naming_result_coherent_false_path() -> None:
    result = NamingResult(
        coherent=False,
        reasoning="cluster too generic",
    )
    assert result.has_emergent_theme() is False
