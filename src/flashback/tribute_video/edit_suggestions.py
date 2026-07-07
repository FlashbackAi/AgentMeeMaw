"""Contextual "edit this tribute" suggestion chips (small gpt-5.1 call).

After a tribute video renders, the family can tap a chip to nudge the next
render -- "More about his fishing trips", "Gentler artwork". The chips are
tailored to THIS subject from the stored memories + message, so they read as
real adjustments rather than generic knobs. Each chip carries a human ``label``
and the ``instruction`` text that gets sent back to ``POST /tributes/{id}/edit``
when tapped.

Best-effort: on any failure (no settings, LLM error, empty output) we return a
small generic fallback catalog so the UI always has something to show.
"""

from __future__ import annotations

from typing import Any

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

log = structlog.get_logger("flashback.tribute_video.edit_suggestions")

# Generic adjustments that apply to any tribute -- the fallback when the LLM
# can't produce subject-specific ones, and the floor the UI always has.
FALLBACK_SUGGESTIONS: list[dict[str, str]] = [
    {"label": "Warmer tone",
     "instruction": "Make the overall tone warmer and more tender."},
    {"label": "Gentler artwork",
     "instruction": "Soften the artwork -- calmer light, gentler brushwork."},
    {"label": "More playful",
     "instruction": "Bring out the lighter, more playful moments where they fit."},
    {"label": "Slower, quieter pace",
     "instruction": "Let the story breathe -- fewer beats, lingering longer."},
    {"label": "Lean on family",
     "instruction": "Lean the arc toward family and the people they loved."},
]

_SYSTEM = """\
You propose short "edit chips" a family can tap to adjust a tribute video about
their loved one. They have seen a draft; each chip is a concrete adjustment they
might want next.

You receive the subject (with relationship), the memories the video is built
from, the contributor's message, and any adjustments already applied. Propose
4-5 chips, each with:
- a `label`: 2-5 words, what the user sees on the button ("More about his
  workshop", "Gentler artwork").
- an `instruction`: ONE imperative sentence telling the storyteller what to
  change -- tone, emphasis, which memories to feature or downplay, or the feel
  of the art.

Rules:
- Ground the SUBJECT-SPECIFIC chips in the actual memories (a place, a craft, a
  relationship that really appears) -- not generic categories.
- Vary the axes: at least one about EMPHASIS (what to feature), one about TONE,
  one about ART feel. Distinct from each other.
- Do NOT repeat an adjustment already applied (see <already_applied>).
- Never invent facts about the subject; only reference what the memories show.
- Imperative, plain, kind. No emojis, no quotes.

Call `propose_edits` exactly once.
"""

_TOOL = ToolSpec(
    name="propose_edits",
    description="Return 4-5 tappable tribute edit chips. Call exactly once.",
    input_schema={
        "type": "object",
        "properties": {
            "suggestions": {
                "type": "array",
                "minItems": 3,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "minLength": 1,
                                  "maxLength": 40},
                        "instruction": {"type": "string", "minLength": 1,
                                        "maxLength": 200},
                    },
                    "required": ["label", "instruction"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["suggestions"],
        "additionalProperties": False,
    },
)


def _user_message(*, subject_name: str, relationship: str | None,
                  candidates: list[dict[str, Any]], message_text: str,
                  prior_instructions: list[str]) -> str:
    rel = f' relationship="{xml_text(relationship)}"' if relationship else ""
    blocks = []
    for m in candidates:
        body = (m.get("narrative") or m.get("title") or "").strip()
        if not body:
            continue
        blocks.append(
            f"<memory>{xml_text((m.get('title') or '').strip())}: "
            f"{xml_text(body)}</memory>")
    msg = (message_text or "").strip()
    msg_block = f"<message>{xml_text(msg)}</message>\n" if msg else ""
    prior = [p for p in (prior_instructions or []) if p and p.strip()]
    prior_block = (
        "<already_applied>\n"
        + "\n".join(f"<request>{xml_text(p)}</request>" for p in prior)
        + "\n</already_applied>\n"
        if prior else ""
    )
    return (
        f"<subject{rel}>{xml_text(subject_name)}</subject>\n"
        f"{msg_block}{prior_block}"
        f"<memories>\n" + "\n".join(blocks) + "\n</memories>"
    )


async def generate_edit_suggestions(
    *,
    settings,
    subject_name: str,
    relationship: str | None,
    candidates: list[dict[str, Any]],
    message_text: str = "",
    prior_instructions: list[str] | None = None,
) -> list[dict[str, str]]:
    """Best-effort contextual edit chips. Returns the fallback on any failure."""
    usable = [c for c in (candidates or []) if (c.get("narrative") or c.get("title"))]
    if settings is None or not usable:
        return list(FALLBACK_SUGGESTIONS)

    user = _user_message(
        subject_name=subject_name, relationship=relationship,
        candidates=usable, message_text=message_text,
        prior_instructions=prior_instructions or [])
    try:
        args = await call_with_tool(
            provider=settings.llm_small_provider,
            model=settings.llm_intent_model,
            system_prompt=_SYSTEM,
            user_message=user,
            tool=_TOOL,
            max_tokens=400,
            timeout=12.0,
            settings=settings,
            feature="tribute_video",
        )
    except LLMError as exc:
        log.warning("edit_suggestions.llm_failed", error=str(exc))
        return list(FALLBACK_SUGGESTIONS)
    except Exception as exc:  # defensive -- never block on suggestion gen
        log.warning("edit_suggestions.unexpected_failure",
                    error_type=type(exc).__name__, detail=str(exc))
        return list(FALLBACK_SUGGESTIONS)

    raw = args.get("suggestions") if isinstance(args, dict) else None
    if not isinstance(raw, list):
        return list(FALLBACK_SUGGESTIONS)
    cleaned: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or "").strip()
        instruction = (item.get("instruction") or "").strip()
        if label and instruction:
            cleaned.append({"label": label, "instruction": instruction})
    return cleaned or list(FALLBACK_SUGGESTIONS)
