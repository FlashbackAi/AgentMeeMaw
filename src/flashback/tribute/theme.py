"""Constants for the on-demand Tribute theme.

The tribute capability is one reusable theme (kind='tribute'), seeded
on demand when a contributor enters the flow -- NOT at person creation,
so normal legacies stay clean (spec section 4). 'Father's Day' is a copy
skin applied in Plan 4; the slug + neutral copy here are campaign-neutral.
"""

from __future__ import annotations

from flashback.themes.archetype_llm import ArchetypeQuestion

TRIBUTE_SLUG = "tribute"
TRIBUTE_DISPLAY_NAME = "A Tribute"
TRIBUTE_DESCRIPTION = (
    "A short, shareable tribute to them -- a handful of shared memories "
    "and one thing you'd want to say straight to them."
)

# Neutral default copy for the message-invitation tap. Plan 4's campaign
# skin (e.g. Father's Day) overrides this string.
MESSAGE_INVITATION_COPY = (
    "If you could say one thing straight to them, what would it be?"
)

# Expanded archetype question count for the tribute theme (universals
# stay at the 3-4 default).
TRIBUTE_ARCHETYPE_MIN = 6
TRIBUTE_ARCHETYPE_MAX = 8

# Compiled-output shape (Plan 3). Video length is skin-configurable in
# Plan 4; this is the neutral default. Storybook is hard-capped with a floor
# below which it won't generate. Cap raised to 13 (cover + up to 12 scenes)
# so a full chronological confession arc has room to breathe -- the prior 9
# compressed a 15-beat story too hard. Scene count still scales down with the
# graph: the assembler only emits scenes it has vivid material for.
VIDEO_TARGET_SECONDS = 45
STORYBOOK_MIN_PAGES = 3
STORYBOOK_MAX_PAGES = 13


# The Father's Day theme's questions (docs/Fathers_Day_Storybook_Brief_v2.md
# §3). These are ephemeral priors (invariant #22): they seed the opener; the
# storybook is assembled from extracted moments, never from these answers.
# (question_text, [option labels]) -- the deeper free-text-first beats still
# ship as MC with starter chips so the card surface is uniform; free-text +
# Skip are always available on the card (ArchetypeQuestion defaults).
FATHERS_DAY_ARCHETYPE_BANK: list[tuple[str, list[str]]] = [
    # -- Layer 1: the world he came from --
    (
        "Where did your father grow up?",
        ["A village", "A small town", "A big city", "Abroad"],
    ),
    (
        "What was your father's main work or trade?",
        ["A trade / manual work", "A salaried job", "His own business", "Farming / land"],
    ),
    (
        "Was his income steady, or did some months stretch thin?",
        ["Steady wage", "Up and down", "Often tight", "We never lacked"],
    ),
    (
        "Was he raised by both parents, or did he lose someone early?",
        ["Both, all through", "Lost his father young", "Lost his mother young", "Raised by others"],
    ),
    # -- Layer 2A: the mirror pairs (his childhood vs. yours) --
    (
        "What kind of clothes did you wear growing up?",
        ["Branded / new", "Hand-me-downs", "Simple but clean", "The best they could afford"],
    ),
    (
        "What did your school or education look like?",
        ["Private / English-medium", "Government school", "Convent", "Far from home"],
    ),
    (
        "How did you get to school each morning?",
        ["He dropped me", "Bus / auto", "Bicycle", "Walked"],
    ),
    (
        "What treats could you reach for freely as a kid?",
        ["Sweets", "Eating out", "Cold drinks", "Whatever I wanted"],
    ),
    # -- Layer 2B: his choices & what he went without --
    (
        "What's something he made sure you had that he never did?",
        ["An education", "A home", "Comfort", "Real choices"],
    ),
    (
        "Did he ever uproot his life or give up something he'd built, for your sake?",
        ["Sold a home", "Left his land", "Changed careers", "Moved everything"],
    ),
    (
        "What did he go without, day to day, while providing?",
        ["Skipped meals", "No small comforts", "No rest", "Spent nothing on himself"],
    ),
    (
        "Did he have money he could have spent on himself but didn't?",
        ["Yes -- always chose us", "Sometimes", "He was genuinely stretched", "Not sure"],
    ),
    (
        "If he'd chosen himself, what could his life have looked like?",
        ["More wealth", "Kept his land", "A bigger career", "His own dreams"],
    ),
    # -- Layer 2D: the confession --
    (
        "What's the one thing you've never said to him out loud?",
        ["I love you", "I'm proud of you", "Thank you", "You're my hero"],
    ),
]


def build_fathers_day_archetype_questions() -> list[ArchetypeQuestion]:
    """The fixed FD bank as ArchetypeQuestion objects (no LLM call)."""
    out: list[ArchetypeQuestion] = []
    for q_idx, (text, labels) in enumerate(FATHERS_DAY_ARCHETYPE_BANK, start=1):
        options = [
            {"option_id": f"q{q_idx}_o{o_idx}", "label": label}
            for o_idx, label in enumerate(labels, start=1)
            if label.strip()
        ]
        if len(options) < 2:
            continue
        out.append(
            ArchetypeQuestion(
                question_id=f"q{q_idx}",
                text=text,
                options=options,
            )
        )
    return out
