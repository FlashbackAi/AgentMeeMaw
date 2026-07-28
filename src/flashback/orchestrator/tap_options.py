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

# Subject gender -> pronoun string for the option-chip prompt. Unknown / "they"
# yields gender-neutral chips (we must not assume he/she for the subject).
_PRONOUNS = {
    "he": "he/him/his",
    "she": "she/her/hers",
    "they": "they/them/theirs",
}


def _pronoun_phrase(gender: str | None) -> str:
    return _PRONOUNS.get((gender or "they").strip().lower(), "they/them/theirs")


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
- PRONOUNS — read the subject's pronouns from <subject pronouns="...">. Use
  ONLY those. If the pronouns are "they/them/theirs" (gender unknown or
  non-binary), keep EVERY chip strictly gender-neutral: never use
  he/him/his/she/her/hers — phrase the memory without a gendered pronoun
  ("Laughing on the couch", "Singing along in the car", "Rolling their eyes
  but smiling") or use they/them. NEVER guess a gender.
- Use a natural register where it fits ("Quiet laugh", "Always on the phone",
  "Cooking for everyone").
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

(The example pronouns above match each example's own subject. For YOUR output,
use only the pronouns given for THIS subject — and when they are they/them,
keep the chips gender-neutral, like the era example, which uses none.)

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
    ground_truth_context: str = "",
    person_gender: str | None = None,
) -> list[str]:
    """Best-effort LLM-driven option chips. Returns ``[]`` on any failure.

    ``person_gender`` (he/she/they; None -> they) controls the pronouns the
    chips may use — unknown/they yields gender-neutral chips so we never
    address the subject as a gender we don't know.
    """

    if settings is None or not question_text:
        return []

    rel_attr = f' relationship="{xml_text(person_relationship)}"' if person_relationship else ""
    pronouns = _pronoun_phrase(person_gender)
    user_block = (
        f'<subject{rel_attr} pronouns="{pronouns}">{xml_text(person_name)}</subject>\n'
        f"<dimension>{xml_text(dimension) if dimension else 'general'}</dimension>\n"
        f"<question>{xml_text(question_text)}</question>"
    )
    if ground_truth_context.strip():
        # Known subject ground truth (region/era/attire) so chips fit the
        # subject's world — saree types, not blazers.
        user_block += (
            f"\n<subject_ground_truth>{xml_text(ground_truth_context)}"
            "</subject_ground_truth>"
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
            feature="tap_options",
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


def _onboarding_prompt(person_name: str) -> str:
    name = person_name or "them"
    return (
        f"When you picture {name}, what's one small, ordinary moment with "
        f"them that's stayed with you?"
    )


async def generate_onboarding_tap(
    *,
    settings,
    person_name: str,
    relationship: str | None,
    person_gender: str | None = None,
) -> tuple[str, list[str]]:
    """Indirect 'defining memory' onboarding prompt + 4 chips.

    The prompt is templated (warm, never a direct 'what did they mean to
    you?'); the chips reuse :func:`generate_tap_options`. Options are ``[]``
    on any failure — the card falls back to prompt + free-text.
    """
    text = _onboarding_prompt(person_name)
    options = await generate_tap_options(
        settings=settings,
        question_text=text,
        person_name=person_name,
        person_relationship=relationship,
        dimension="",
        person_gender=person_gender,
    )
    return text, options
