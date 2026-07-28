"""The single small-LLM call behind contextual ground-truth taps.

Given the unknown askable fields, the rolling summary, and the most
recent turns, it returns one of: skip (nothing natural to ask, or the
answer is already evident in this conversation — the "Hyderabad rule"),
a person-field question, or a segment time-anchor question. Best-effort:
any failure returns None and the turn proceeds untouched (mirrors
``tap_options``).
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from flashback.ground_truth.registry import GroundTruthField
from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

log = structlog.get_logger("flashback.ground_truth.selection")

_SELECT_GT_SYSTEM = """\
You decide whether to surface ONE small tap-card question beneath the
agent's reply, while a contributor is mid-story about a loved one. The
card captures stable ground truth about the SUBJECT (where their life
happened, what they wore, what they looked like) or anchors the story
being told in time. The user taps one chip — it must never feel like a
form or a survey.

You are given: the fields still unknown, what is already known, the
rolling summary of this session, and the most recent turns.

Decision rules, in order:
1. If the answer to a candidate field is ALREADY evident anywhere in
   this conversation (rolling summary or recent turns), do NOT ask it —
   it will be inferred. (E.g. they said "Hyderabad": region is evident.)
2. Only ask a field the CURRENT story naturally touches. A question
   about clothing during a story set at home is natural; a question
   about languages during a story about a road trip is not. If no
   unknown field is touched by the live story, skip.
3. If anchor is allowed and the story being told has NO time signal at
   all (no year, age, decade, or life-period mention), an anchor
   question ("About when was that?") may be the most natural ask.
4. When in doubt, skip. Skipping is free; a misplaced question is not.

Question style: one short sentence of fond curiosity about the story
("Where was that house?", "What would she have been wearing back
then?"). NEVER clinical ("What is her skin tone?" is banned — never ask
about complexion, ethnicity, or race). Options follow the chip rules:
exactly 4, each 2-6 words, concrete memory-fragment register, no
taxonomic buckets, no proper nouns you weren't given.

Call the `select_ground_truth_question` tool exactly once.
"""

_SELECT_GT_TOOL = ToolSpec(
    name="select_ground_truth_question",
    description="Choose skip, one person-field question, or one anchor question.",
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["skip", "ask_field", "ask_anchor"],
            },
            "field": {
                "type": "string",
                "description": "Registry key. Required when action=ask_field.",
            },
            "question_text": {"type": "string", "maxLength": 200},
            "options": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "items": {"type": "string", "minLength": 1, "maxLength": 60},
            },
            "reason": {"type": "string", "maxLength": 200},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)


async def select_ground_truth_question(
    *,
    settings,
    person_name: str,
    person_relationship: str | None,
    unknown_fields: list[GroundTruthField],
    known_block: str,
    rolling_summary: str,
    recent_turns: list[tuple[str, str]],
    anchor_allowed: bool,
) -> dict[str, Any] | None:
    """Returns the validated tool args dict, or None on skip/failure."""
    if settings is None or (not unknown_fields and not anchor_allowed):
        return None

    field_lines = "\n".join(
        f"- {f.key}: {f.description} (e.g. \"{f.example_question}\")"
        for f in unknown_fields
    )
    turn_lines = "\n".join(
        f"{role}: {xml_text(content)}" for role, content in recent_turns
    )
    rel_attr = (
        f' relationship="{xml_text(person_relationship)}"'
        if person_relationship
        else ""
    )
    user_block = "\n".join(
        [
            f"<subject{rel_attr}>{xml_text(person_name)}</subject>",
            f"<anchor_allowed>{str(anchor_allowed).lower()}</anchor_allowed>",
            "<unknown_fields>",
            field_lines or "(none)",
            "</unknown_fields>",
            "<known_ground_truth>",
            xml_text(known_block) or "(nothing known yet)",
            "</known_ground_truth>",
            "<rolling_summary>",
            xml_text(rolling_summary or ""),
            "</rolling_summary>",
            "<recent_turns>",
            turn_lines,
            "</recent_turns>",
        ]
    )

    try:
        args = await call_with_tool(
            provider=settings.llm_small_provider,
            model=settings.llm_intent_model,
            system_prompt=_SELECT_GT_SYSTEM,
            user_message=user_block,
            tool=_SELECT_GT_TOOL,
            max_tokens=300,
            timeout=10.0,
            settings=settings,
            feature="ground_truth_tap",
        )
    except LLMError as exc:
        log.warning("gt_selection.llm_failed", error=str(exc))
        return None
    except Exception as exc:  # defensive — never block a turn on tap selection
        log.warning(
            "gt_selection.unexpected_failure",
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return None

    if not isinstance(args, dict) or args.get("action") == "skip":
        return None
    action = args.get("action")
    if action == "ask_field":
        valid_keys = {f.key for f in unknown_fields}
        if args.get("field") not in valid_keys or not args.get("question_text"):
            return None
    elif action == "ask_anchor":
        if not anchor_allowed or not args.get("question_text"):
            return None
    else:
        return None
    return args


def derive_anchor_chips(birth_era: str | None) -> list[str]:
    """Deterministic anchor chips. With birth_era known, chips map to
    concrete decades; otherwise era-neutral fallbacks."""
    match = re.match(r"^\s*(\d{4})", birth_era or "")
    if match:
        start = int(match.group(1))
        return [
            "When they were young",
            f"In the {start + 20}s",
            f"In the {start + 30}s",
            "Later in life",
        ]
    return [
        "Before I was born",
        "When I was a kid",
        "More recently",
        "Not sure",
    ]
