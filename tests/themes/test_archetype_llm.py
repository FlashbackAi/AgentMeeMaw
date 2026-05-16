"""Tool-schema validity for the theme archetype question generator."""

from __future__ import annotations

import jsonschema

from flashback.themes.archetype_llm import (
    ARCHETYPE_PROMPT_VERSION,
    ArchetypeQuestion,
    _ARCHETYPE_TOOL,
)


def test_archetype_tool_schema_is_valid_json_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(_ARCHETYPE_TOOL.input_schema)


def test_archetype_tool_enforces_3_to_4_questions() -> None:
    qs = _ARCHETYPE_TOOL.input_schema["properties"]["questions"]
    assert qs["minItems"] == 3
    assert qs["maxItems"] == 4


def test_archetype_tool_enforces_exactly_four_options_per_question() -> None:
    opts = _ARCHETYPE_TOOL.input_schema["properties"]["questions"]["items"][
        "properties"
    ]["options"]
    assert opts["minItems"] == 4
    assert opts["maxItems"] == 4


def test_archetype_prompt_version_format() -> None:
    assert ARCHETYPE_PROMPT_VERSION == "theme_archetype.v1"


def test_archetype_question_to_payload_round_trip() -> None:
    q = ArchetypeQuestion(
        question_id="q1",
        text="What role did they play at home?",
        options=[
            {"option_id": "q1_o1", "label": "The provider"},
            {"option_id": "q1_o2", "label": "The peacemaker"},
            {"option_id": "q1_o3", "label": "The storyteller"},
            {"option_id": "q1_o4", "label": "The disciplinarian"},
        ],
    )
    payload = q.to_payload()
    assert payload["question_id"] == "q1"
    assert payload["allow_skip"] is True
    assert payload["allow_free_text"] is True
    assert len(payload["options"]) == 4
