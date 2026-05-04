"""Prompts and tool schemas for P2/P3/P5 question generation."""

from __future__ import annotations

from flashback.llm.tool_spec import ToolSpec


P2_SYSTEM_PROMPT = """\
You are Producer P2 for Flashback, a memorial conversation agent.
Your job is to surface entities - people, places, objects, or
organizations - that the contributor mentioned but did not fully
explore, and generate questions that would open them up next time.

You will be given UNDER-DEVELOPED ENTITIES. Generate 1-2 questions per
entity, targeting that specific entity.

CRITICAL CONSTRAINTS:
- Reference the entity by name.
- Ask for a concrete story, sensory detail, relationship, ritual, or
  moment that is not already captured.
- Every question must include at least one short theme tag.
- Do not produce generic "tell me more about X" questions.
- Skip entities that are too thin to ask about meaningfully.

Respond ONLY by calling the produce_p2_questions tool.
"""


P3_SYSTEM_PROMPT = """\
You are Producer P3 for Flashback, a memorial conversation agent.
Your job is to find gaps in the subject's remembered chronology and
generate warm questions that invite stories from those missing periods.

You will be given LIFE-PERIOD GAPS. Each gap is either a decade such as
"1970s" or a life period such as "early career".

Generate 3-5 questions per gap. Phrase them gently and generically
because the system does not know the exact facts yet.

CRITICAL CONSTRAINTS:
- Each question must include a life_period field matching an input gap.
- Every question must include at least one short theme tag.
- Do not ask for date of birth or date of death.
- Avoid survey phrasing; invite memory, not a form answer.

Respond ONLY by calling the produce_p3_questions tool.
"""


P5_SYSTEM_PROMPT = """\
You are Producer P5 for Flashback, a memorial conversation agent.
Your job is to broaden coverage across universal life dimensions such
as childhood, family, work, marriage, food, faith, losses, sayings, and
daily routines.

You will be given UNDER-COVERED DIMENSIONS. Generate 1-2 questions per
dimension.

CRITICAL CONSTRAINTS:
- Each question must include a dimension field matching an input
  universal dimension.
- Every question must include at least one short theme tag.
- Keep the tone human and concrete.
- Do not make the set feel like a survey.

Respond ONLY by calling the produce_p5_questions tool.
"""


def _themes_schema() -> dict:
    return {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
    }


P2_TOOL = ToolSpec(
    name="produce_p2_questions",
    description="Generate questions targeting under-developed entities.",
    input_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "targets_entity_id": {
                            "type": "string",
                            "format": "uuid",
                            "description": "Entity id from the input list.",
                        },
                        "themes": _themes_schema(),
                    },
                    "required": ["text", "targets_entity_id", "themes"],
                    "additionalProperties": False,
                },
            },
            "overall_reasoning": {"type": "string"},
        },
        "required": ["questions", "overall_reasoning"],
        "additionalProperties": False,
    },
)


P3_TOOL = ToolSpec(
    name="produce_p3_questions",
    description="Generate questions for missing life periods or decades.",
    input_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "life_period": {
                            "type": "string",
                            "description": "The input gap label this question covers.",
                        },
                        "themes": _themes_schema(),
                    },
                    "required": ["text", "life_period", "themes"],
                    "additionalProperties": False,
                },
            },
            "overall_reasoning": {"type": "string"},
        },
        "required": ["questions", "overall_reasoning"],
        "additionalProperties": False,
    },
)


P5_TOOL = ToolSpec(
    name="produce_p5_questions",
    description="Generate questions for under-covered universal dimensions.",
    input_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "dimension": {
                            "type": "string",
                            "description": "Universal dimension from the input list.",
                        },
                        "themes": _themes_schema(),
                    },
                    "required": ["text", "dimension", "themes"],
                    "additionalProperties": False,
                },
            },
            "overall_reasoning": {"type": "string"},
        },
        "required": ["questions", "overall_reasoning"],
        "additionalProperties": False,
    },
)

