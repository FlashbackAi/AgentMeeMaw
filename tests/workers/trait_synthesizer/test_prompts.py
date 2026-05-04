"""Drift detectors for the Trait Synthesizer prompt and tool schema."""

from __future__ import annotations

from typing import get_args

import jsonschema

from flashback.workers.trait_synthesizer.prompts import SYNTH_TOOL, SYSTEM_PROMPT
from flashback.workers.trait_synthesizer.schema import (
    STRENGTH_LADDER,
    Action,
    Strength,
)


def test_tool_schema_is_valid_json_schema() -> None:
    """The tool input schema must be a syntactically valid JSON Schema."""
    jsonschema.Draft202012Validator.check_schema(SYNTH_TOOL.input_schema)


def test_strength_enum_matches_literal_and_ladder() -> None:
    """Tool's initial_strength enum, the Literal, and the ladder must agree."""
    schema = SYNTH_TOOL.input_schema
    enum = schema["properties"]["new_trait_proposals"]["items"]["properties"][
        "initial_strength"
    ]["enum"]
    literal_values = list(get_args(Strength))
    assert enum == literal_values
    assert tuple(enum) == STRENGTH_LADDER


def test_action_enum_matches_literal() -> None:
    schema = SYNTH_TOOL.input_schema
    enum = schema["properties"]["existing_trait_decisions"]["items"][
        "properties"
    ]["action"]["enum"]
    assert enum == list(get_args(Action))


def test_new_trait_proposals_supporting_thread_ids_min_items_1() -> None:
    """Each new trait must cite at least one supporting thread."""
    schema = SYNTH_TOOL.input_schema
    items = schema["properties"]["new_trait_proposals"]["items"]
    assert items["properties"]["supporting_thread_ids"]["minItems"] == 1


def test_existing_trait_decision_required_fields() -> None:
    items = SYNTH_TOOL.input_schema["properties"]["existing_trait_decisions"][
        "items"
    ]
    assert set(items["required"]) == {"trait_id", "action", "reasoning"}


def test_new_trait_proposal_required_fields() -> None:
    items = SYNTH_TOOL.input_schema["properties"]["new_trait_proposals"]["items"]
    assert set(items["required"]) == {
        "name",
        "description",
        "initial_strength",
        "supporting_thread_ids",
        "reasoning",
    }


def test_top_level_required_fields() -> None:
    assert set(SYNTH_TOOL.input_schema["required"]) == {
        "existing_trait_decisions",
        "new_trait_proposals",
        "overall_reasoning",
    }


def test_system_prompt_mentions_keep_default_and_subject_guard() -> None:
    """Sanity: the prompt explicitly biases toward `keep` and names the
    subject identity guard. These are load-bearing for product behaviour."""
    assert "keep" in SYSTEM_PROMPT
    assert "DEFAULT" in SYSTEM_PROMPT
    assert "SUBJECT" in SYSTEM_PROMPT
    assert "synthesize_traits" in SYSTEM_PROMPT
