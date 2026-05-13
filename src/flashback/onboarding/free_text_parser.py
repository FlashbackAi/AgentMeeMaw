"""Small LLM parser for onboarding free-text answers."""

from __future__ import annotations

from typing import Any

import structlog

from flashback.llm.interface import Provider, call_with_tool
from flashback.llm.prompt_safety import tagged
from flashback.llm.tool_spec import ToolSpec
from flashback.onboarding.archetypes import sanitize_implies

log = structlog.get_logger("flashback.onboarding.free_text_parser")


PARSE_FREE_TEXT_TOOL = ToolSpec(
    name="parse_onboarding_answer",
    description=(
        "Parse a short onboarding free-text answer into implied entities, "
        "coverage dimensions, and optional life period."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["person", "place", "object", "organization"],
                        },
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "attributes": {"type": "object"},
                    },
                    "required": ["type", "name"],
                    "additionalProperties": False,
                },
            },
            "coverage": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["sensory", "voice", "place", "relation", "era"],
                },
            },
            "life_period_estimate": {
                "type": "string",
                "description": (
                    "Short life-period label only when directly implied, "
                    "e.g. childhood, school years, early career."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "One short phrase for logs.",
            },
        },
        "required": ["entities", "coverage", "reasoning"],
        "additionalProperties": False,
    },
)


SYSTEM_PROMPT = """\
You parse one short onboarding answer for Flashback, a legacy
preservation system. The subject may be living, deceased, or known only
through inherited family stories. Never assume their status.

Return only concrete implications that are directly present in the
answer. Under-extract when unsure.

Coverage dimensions:
- sensory: a concrete sensory detail, object, image, sound, smell, habit
- voice: speech, manner, phrase, temperament, or communication style
- place: a place or setting
- relation: another person or relationship dynamic
- era: a time period, age, life stage, school/work period, or sequence

Entity rules:
- Extract named or clearly implied people, places, objects, and
  organizations.
- Do not extract the legacy subject as an entity.
- For inherited ancestor stories, capture the storyteller or family
  relation when the answer names one.
- Keep names short and literal. Do not invent proper names.

Life period:
- Set life_period_estimate only when the answer clearly implies one
  ("school", "childhood", "first job", "after marriage").
"""


async def parse_free_text_answer(
    *,
    settings,
    provider: Provider,
    model: str,
    timeout: float,
    max_tokens: int,
    relationship: str | None,
    question_text: str,
    free_text: str,
) -> dict[str, Any]:
    """Return normalized implies for one free-text onboarding answer."""

    user_message = "\n".join(
        [
            tagged("relationship", relationship or ""),
            tagged("question", question_text),
            tagged("answer", free_text),
        ]
    )
    args = await call_with_tool(
        provider=provider,
        model=model,
        system_prompt=SYSTEM_PROMPT,
        user_message=user_message,
        tool=PARSE_FREE_TEXT_TOOL,
        max_tokens=max_tokens,
        timeout=timeout,
        settings=settings,
    )
    implies = sanitize_implies(args)
    log.info(
        "onboarding.free_text_parsed",
        coverage=implies.get("coverage", []),
        entities=len(implies.get("entities", [])),
    )
    return implies
