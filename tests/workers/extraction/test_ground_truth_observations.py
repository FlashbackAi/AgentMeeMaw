from flashback.workers.extraction.extraction_llm import _build_user_message
from flashback.workers.extraction.prompts import EXTRACTION_TOOL
from flashback.workers.extraction.schema import (
    ExtractionResult,
    SegmentAnchor,
)


def test_extraction_result_parses_observations():
    result = ExtractionResult.model_validate({
        "moments": [], "entities": [], "traits": [],
        "dropped_references": [], "extraction_notes": "",
        "ground_truth_observations": [
            {"field": "region", "value": "Karimnagar, Telangana, India",
             "confidence": "high"},
        ],
    })
    assert result.ground_truth_observations[0].field == "region"


def test_extraction_result_defaults_observations_empty():
    result = ExtractionResult.model_validate({
        "moments": [], "entities": [], "traits": [],
        "dropped_references": [], "extraction_notes": "",
    })
    assert result.ground_truth_observations == []


def test_tool_schema_includes_observations_with_field_enum():
    props = EXTRACTION_TOOL.input_schema["properties"]
    obs = props["ground_truth_observations"]
    field_enum = obs["items"]["properties"]["field"]["enum"]
    assert "region" in field_enum
    assert "era_span" not in field_enum  # code-derived, never LLM-emitted


def test_user_message_renders_ground_truth_and_anchor_blocks():
    msg = _build_user_message(
        subject_name="Ishita",
        subject_relationship=None,
        prior_rolling_summary="",
        segment_turns=[],
        ground_truth_block="region: Karimnagar, Telangana, India",
        segment_anchor=SegmentAnchor(
            question_text="About when was that?", answer="In the 1970s"
        ),
    )
    assert "<subject_ground_truth>" in msg
    assert "Karimnagar" in msg
    assert "<segment_time_anchor>" in msg
    assert "In the 1970s" in msg


def test_user_message_omits_blocks_when_absent():
    msg = _build_user_message(
        subject_name="Ishita", subject_relationship=None,
        prior_rolling_summary="", segment_turns=[],
    )
    assert "<subject_ground_truth>" not in msg
    assert "<segment_time_anchor>" not in msg
