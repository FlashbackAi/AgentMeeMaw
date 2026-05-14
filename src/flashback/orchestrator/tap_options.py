"""Generate tappable answer chips for a coverage / starter tap question.

The contributor sees a tap card mid-chat that mirrors the archetype
onboarding shape: a question, 4 short tappable option chips, and a
free-text fallback. The option chips come from a small gpt-5.1 call
that knows the question, the subject's name + relationship, and the
gap dimension. We don't store options in the questions table — they
are regenerated each time a tap fires so they stay contextual.

Best-effort: returns ``[]`` on any failure. The card falls back to
question + free-text when options are unavailable.
"""

from __future__ import annotations

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.tool_spec import ToolSpec
from flashback.llm.prompt_safety import xml_text

log = structlog.get_logger("flashback.tap_options")


_TAP_OPTIONS_SYSTEM = """\
You generate 4 short tappable answer chips for a follow-up question
about a person. The contributor sees them as buttons under the
question and can tap one to jumpstart their answer, or type their own.

The chips are NOT taxonomic categories. They are concrete first-line
answers a person might actually give about a parent, sibling, friend,
or other loved one. Think "what would a real person blurt out", not
"what are the 4 buckets of possible answers".

Rules:
- Output EXACTLY 4 chips. No more, no fewer.
- Each chip is 2-6 words. Read like a memory fragment, not a label.
- Use first-person register where it fits ("His quiet laugh",
  "Always on the phone", "Cooking for everyone").
- Sensory detail beats abstraction. Concrete beats generic. Verbs
  beat nouns when natural.
- Don't enumerate exhaustive categories. Pick 4 that feel emotionally
  textured and distinct from each other.
- NEVER invent proper nouns, dates, places, or specific quotes about
  the subject. Stay generic on identity, concrete on shape.
- Avoid abstract bucket labels: NO "Friendly greeting", "Asking a
  casual question", "Making a joke", "Particular laugh",
  "Specific catchphrase", "Talking about shared interests". These
  read as taxonomies — they are banned.
- Match the dimension hint loosely; do not let it produce generic
  category names.
  * sensory  → physical / visual textures (a smile, the eyes, hands
               always moving, the way they sat in a room)
  * voice    → how they sounded or spoke (a quiet voice, always
               telling stories, a laugh that filled the room)
  * place    → where they spent time (the kitchen, on their porch,
               outdoors, at work)
  * relation → who they were close to (a parent or sibling, a partner,
               a close friend)
  * era      → moments in time (childhood, the working years, after
               retirement)

Examples of the right shape:

Question: "When you picture her, what do you see?"
Dimension: sensory
Output: ["Her quick smile", "Always in the kitchen", "Reading by the window", "Hands always moving"]

Question: "Is there a way he talks that stands out?"
Dimension: voice
Output: ["He laughs through it", "Stories that go on forever", "Always asking questions back", "Quiet but pointed"]

Question: "Where did she grow up?"
Dimension: place
Output: ["Same city as me", "A small town she still misses", "Moved around a lot", "Far from where I am"]

Question: "What did weekends look like?"
Dimension: era
Output: ["Long mornings at home", "Out the door early", "Cooking the whole day", "Visiting family"]

Bad output (do not produce):
- ["Friendly greeting", "Asking a casual question", "Making a joke", "Shared interests"]  ← taxonomic
- ["Specific catchphrase", "Particular laugh", "Unique voice", "Way of telling stories"]  ← labels
- ["Yes", "No", "Sometimes", "Not sure"]                                                  ← polar
- ["Mom", "Dad", "Brother", "Sister"]                                                     ← exhaustive list

Call the `generate_options` tool exactly once.
"""


_TAP_OPTIONS_TOOL = ToolSpec(
    name="generate_options",
    description=(
        "Generate 4 short tappable answer chips for the question. "
        "Call exactly once."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "items": {"type": "string", "minLength": 1, "maxLength": 60},
            },
        },
        "required": ["options"],
        "additionalProperties": False,
    },
)


async def generate_tap_options(
    *,
    settings,
    question_text: str,
    person_name: str,
    person_relationship: str | None,
    dimension: str,
) -> list[str]:
    """Best-effort LLM-driven option chips. Returns ``[]`` on any failure."""

    if settings is None or not question_text:
        return []

    rel_attr = f' relationship="{xml_text(person_relationship)}"' if person_relationship else ""
    user_block = (
        f"<subject{rel_attr}>{xml_text(person_name)}</subject>\n"
        f"<dimension>{xml_text(dimension) if dimension else 'general'}</dimension>\n"
        f"<question>{xml_text(question_text)}</question>"
    )

    try:
        args = await call_with_tool(
            provider=settings.llm_small_provider,
            model=settings.llm_intent_model,
            system_prompt=_TAP_OPTIONS_SYSTEM,
            user_message=user_block,
            tool=_TAP_OPTIONS_TOOL,
            max_tokens=200,
            timeout=10.0,
            settings=settings,
        )
    except LLMError as exc:
        log.warning("tap_options.llm_failed", error=str(exc))
        return []
    except Exception as exc:  # defensive — never block a tap on option gen
        log.warning(
            "tap_options.unexpected_failure",
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return []

    raw = args.get("options") if isinstance(args, dict) else None
    if not isinstance(raw, list):
        return []
    cleaned: list[str] = []
    for item in raw:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                cleaned.append(stripped)
    return cleaned[:4]
