from __future__ import annotations

import pytest
from pydantic import ValidationError

from flashback.llm.tool_spec import ToolSpec


def test_tool_spec_validates_required_fields():
    with pytest.raises(ValidationError):
        ToolSpec(name="only_name")


def test_tool_spec_serializes_for_provider_handoff():
    spec = ToolSpec(
        name="classify_intent",
        description="Classify the turn.",
        input_schema={
            "type": "object",
            "properties": {"intent": {"type": "string"}},
            "required": ["intent"],
        },
    )

    assert spec.model_dump() == {
        "name": "classify_intent",
        "description": "Classify the turn.",
        "input_schema": {
            "type": "object",
            "properties": {"intent": {"type": "string"}},
            "required": ["intent"],
        },
    }
