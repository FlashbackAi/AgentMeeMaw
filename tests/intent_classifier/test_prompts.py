from __future__ import annotations

from typing import get_args

import jsonschema

from flashback.intent_classifier.prompts import INTENT_TOOL, SYSTEM_PROMPT
from flashback.intent_classifier.schema import Confidence, Intent, IntentResult, Temperature


def test_intent_tool_input_schema_is_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(INTENT_TOOL.input_schema)


def test_intent_tool_required_fields_match_result_model():
    assert set(INTENT_TOOL.input_schema["required"]) == set(IntentResult.model_fields)


def test_intent_values_match_literal():
    tool_values = INTENT_TOOL.input_schema["properties"]["intent"]["enum"]
    assert set(tool_values) == set(get_args(Intent))


def test_confidence_values_match_literal():
    tool_values = INTENT_TOOL.input_schema["properties"]["confidence"]["enum"]
    assert set(tool_values) == set(get_args(Confidence))


def test_temperature_values_match_literal():
    tool_values = INTENT_TOOL.input_schema["properties"]["emotional_temperature"]["enum"]
    assert set(tool_values) == set(get_args(Temperature))


def test_system_prompt_documents_outcomes_per_intent():
    """The OUTCOMES section makes the classifier reason over downstream
    response shape, not just input signal. Every intent must be named so
    the LLM cannot silently rely on a stale definition."""
    assert "OUTCOMES" in SYSTEM_PROMPT
    for intent in get_args(Intent):
        assert f"`{intent}`" in SYSTEM_PROMPT
