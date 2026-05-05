from __future__ import annotations

import jsonschema
import pytest
from pydantic import ValidationError

from flashback.segment_detector.prompts import (
    SEGMENT_DETECTOR_TOOL,
    SYSTEM_PROMPT_FORCE,
    SYSTEM_PROMPT_NORMAL,
)
from flashback.segment_detector.schema import SegmentDetectionResult


def test_segment_detector_tool_input_schema_is_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(
        SEGMENT_DETECTOR_TOOL.input_schema
    )


def test_segment_detector_tool_required_fields_are_conditional_summary_shape():
    required = set(SEGMENT_DETECTOR_TOOL.input_schema["required"])

    assert required == {"boundary_detected", "reasoning"}
    assert "rolling_summary" not in required


def test_schema_rejects_boundary_without_rolling_summary():
    with pytest.raises(ValidationError):
        SegmentDetectionResult(
            boundary_detected=True,
            reasoning="The topic has closed.",
        )


def test_schema_accepts_no_boundary_without_rolling_summary():
    result = SegmentDetectionResult(
        boundary_detected=False,
        reasoning="The user is still adding detail.",
    )

    assert result.rolling_summary is None


def test_system_prompts_are_non_empty_and_distinct():
    assert SYSTEM_PROMPT_NORMAL.strip()
    assert SYSTEM_PROMPT_FORCE.strip()
    assert SYSTEM_PROMPT_NORMAL != SYSTEM_PROMPT_FORCE


def test_normal_prompt_closes_when_agent_pivots_after_switch():
    assert "agent's latest response has just pivoted away" in SYSTEM_PROMPT_NORMAL
    assert "Close the previous" in SYSTEM_PROMPT_NORMAL
    assert "do not wait for the contributor to answer the new prompt" in (
        SYSTEM_PROMPT_NORMAL
    )
