"""Generate-first authoring: a brief -> a structured config draft (big LLM).

The content person gives the occasion + relationship + a 1-3 sentence brief;
the LLM fills the SAME structured schema the CRM form edits. Output is a
draft for human tuning — the route validates it with the Task-2 validators
and never writes it live. Third-person address is enforced in the prompt so
a future address_mode stays additive (spec §3.5).
"""

from __future__ import annotations

from typing import Literal

from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

_BANK_SCHEMA = {
    "type": "array",
    "minItems": 8,
    "maxItems": 12,
    "items": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "minLength": 5, "maxLength": 160},
            "options": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "items": {"type": "string", "minLength": 1, "maxLength": 40},
            },
        },
        "required": ["question", "options"],
        "additionalProperties": False,
    },
}

_PROFILE_TOOL = ToolSpec(
    name="draft_relationship_profile",
    description="Return the structured relationship-profile draft. Once.",
    input_schema={
        "type": "object",
        "properties": {
            "display_name": {"type": "string", "maxLength": 40},
            "synonyms": {
                "type": "array",
                "items": {"type": "string", "maxLength": 40},
                "maxItems": 20,
            },
            "voice": {
                "type": "object",
                "properties": {
                    "energy_words": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 6,
                        "items": {"type": "string", "maxLength": 30},
                    },
                    "narrator_stance": {"type": "string", "maxLength": 160},
                    "emotion_rule": {"type": "string", "maxLength": 200},
                    "never": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 60},
                        "maxItems": 6,
                    },
                },
                "required": ["energy_words", "narrator_stance", "emotion_rule",
                             "never"],
                "additionalProperties": False,
            },
            "opener": {
                "type": "object",
                "properties": {
                    "style": {"type": "string", "maxLength": 220},
                    "examples": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 3,
                        "items": {"type": "string", "maxLength": 120},
                    },
                },
                "required": ["style", "examples"],
                "additionalProperties": False,
            },
            "art": {
                "type": "object",
                "properties": {
                    "mood_words": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 8,
                        "items": {"type": "string", "maxLength": 40},
                    },
                    "avoid": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 40},
                        "maxItems": 6,
                    },
                },
                "required": ["mood_words", "avoid"],
                "additionalProperties": False,
            },
            "narrative": {
                "type": "object",
                "properties": {
                    "audience": {"type": "string", "maxLength": 200},
                    "arc": {"type": "string", "maxLength": 240},
                    "throughline": {"type": "string", "maxLength": 200},
                },
                "required": ["audience", "arc", "throughline"],
                "additionalProperties": False,
            },
            "fallback_opener": {"type": "string", "maxLength": 120},
            "fallback_closing": {"type": "string", "maxLength": 120},
            "archetype_bank": _BANK_SCHEMA,
            "message_invitation_copy": {"type": "string", "maxLength": 200},
        },
        "required": ["display_name", "voice", "opener", "art", "narrative",
                     "fallback_opener", "fallback_closing", "archetype_bank",
                     "message_invitation_copy"],
        "additionalProperties": False,
    },
)

_CAMPAIGN_TOOL = ToolSpec(
    name="draft_campaign",
    description="Return the structured occasion-campaign draft. Once.",
    input_schema={
        "type": "object",
        "properties": {
            "display_name": {"type": "string", "maxLength": 60},
            "message_card_copy": {"type": "string", "maxLength": 220},
            "archetype_extra_context": {"type": "string", "maxLength": 400},
            "archetype_bank_override": _BANK_SCHEMA,
        },
        "required": ["display_name", "message_card_copy",
                     "archetype_extra_context"],
        "additionalProperties": False,
    },
)

_SYSTEM = """\
You draft tribute-video configuration for a legacy-preservation product. A
"tribute" is a short, emotional storybook video a contributor makes about
someone they love; your draft controls its voice, opener register, art mood
and the up-front multiple-choice questions that gather material.

HARD RULES:
- THIRD-PERSON address only: the video speaks ABOUT the subject ("he/she/
  they/{name}"), never TO them. No "you/your" aimed at the subject anywhere.
- Every opener example AND both fallback lines MUST contain the literal
  placeholder {name}.
- Openers must fit the relationship's register: peers (friends, cousins,
  siblings) never get formal introductions like "Meet my friend" — open like
  a story told at a party. Elders may get a dedication register.
- Question options: at most 5 words each, concrete, chip-sized. Questions
  build a narrative arc (how they met -> texture of shared life -> what was
  never said out loud).
- Indian-context sensibility (kin terms, chai stalls, festivals) is welcome
  where natural; never stereotyped.
- The emotion rule should say WHERE the feeling lives and when sincerity is
  allowed to break through.
- NARRATIVE framing must fit the relationship, NOT default to a eulogy:
  * audience -- who watches and is spoken to (for a peer/friend the audience
    often includes the subject themselves and the shared circle; for an elder
    memorial it may be someone who never met them).
  * arc -- the SHAPE the pages follow. A friendship is "how you met -> the
    everyday rituals -> drifting apart -> the reunion -> what they still mean",
    NOT a birth-to-late-years life arc. Only elders/memorials get a life arc.
  * throughline -- what the piece is ultimately about (a friendship's shared
    jokes and loyalties; a parent's quiet greatness; etc). Never force
    "their life and greatness" onto a peer relationship.

Call the tool once with the complete draft."""


async def generate_config_draft(
    settings,
    *,
    kind: Literal["profile", "campaign"],
    relationship_group: str | None = None,
    occasion: str | None = None,
    brief: str,
) -> dict:
    """Big-LLM structured draft; raises LLMError upward (route maps to 502)."""
    parts = []
    if occasion:
        parts.append(f"<occasion>{xml_text(occasion)}</occasion>")
    if relationship_group:
        parts.append(
            f"<relationship_group>{xml_text(relationship_group)}"
            "</relationship_group>"
        )
    parts.append(f"<brief>{xml_text(brief)}</brief>")
    parts.append(
        "Draft a "
        + ("relationship profile." if kind == "profile" else
           "campaign wrapper for this occasion.")
    )
    tool = _PROFILE_TOOL if kind == "profile" else _CAMPAIGN_TOOL
    args = await call_with_tool(
        provider=settings.llm_big_provider,
        model=settings.llm_big_model,
        system_prompt=_SYSTEM,
        user_message="\n".join(parts),
        tool=tool,
        max_tokens=4000,
        timeout=60.0,
        settings=settings,
        feature="tribute_config_generate",
    )
    return args if isinstance(args, dict) else {}
