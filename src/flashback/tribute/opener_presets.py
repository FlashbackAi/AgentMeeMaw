"""Curated opening-style presets for the tribute opener.

Agent-owned catalog (like the layout catalog / motion presets): the CRM shows
these as a dropdown and stores the chosen ``slug`` on the profile's ``opener``
JSONB (``opener.preset``). The composer resolves the slug to a style + example
lines at compose time, so changing a preset here updates every profile that
picked it -- "author once, available everywhere". A profile with no preset
falls back to its free-text ``opener.style`` / ``opener.examples`` (the
pre-preset path), so nothing existing breaks.

Product invariant: the video speaks ABOUT the subject (he/she/name), never TO
them -- so every preset stays third-person / narrator-first-person. No preset
addresses the subject as "you".

Example lines carry ``{name}`` (and may carry ``{relationship}``); the
assembler substitutes both onto the real subject.
"""
from __future__ import annotations

OPENER_PRESETS: list[dict] = [
    {
        "slug": "dedication",
        "label": "Dedication (classic)",
        "description": "A warm formal naming — the memorial default.",
        "style": (
            "open with a warm dedication that names them and, in a breath, who "
            "they were and why they mattered — a dedication, not a memory"
        ),
        "examples": [
            "Meet my {relationship}, {name} — the quietest strong person I know.",
            "This is {name}, and this is the life they built.",
        ],
    },
    {
        "slug": "party_story",
        "label": "Party-story tease",
        "description": "Open like the first line of a story told at every party — a tease or mock-complaint.",
        "style": (
            "open like the first line of a story told at every party: a tease, "
            "a mock-complaint, a dare — never a formal introduction"
        ),
        "examples": [
            "Nobody warned me about {name}.",
            "Some people you meet. {name} I got stuck with — best thing that ever happened.",
        ],
    },
    {
        "slug": "scene_setter",
        "label": "Scene-setter",
        "description": "Open on a vivid moment or time that drops you straight into their world.",
        "style": (
            "open by setting a vivid scene — a time, a place, a moment — that "
            "drops the viewer straight into who they were"
        ),
        "examples": [
            "It started the year {name} showed up with a cricket bat and no plan.",
            "Every evening on that veranda, {name} held court.",
        ],
    },
    {
        "slug": "bold_claim",
        "label": "Bold claim",
        "description": "A big, confident statement about who they were.",
        "style": (
            "open with one bold, confident claim about who they were that the "
            "rest of the film goes on to prove"
        ),
        "examples": [
            "{name} could talk a stranger into anything.",
            "There was no room {name} couldn't warm in under a minute.",
        ],
    },
    {
        "slug": "quiet_open",
        "label": "Quiet & tender",
        "description": "Understated and intimate — a soft, low opening.",
        "style": (
            "open quietly and tender — understated, intimate, a soft low line "
            "rather than a big statement"
        ),
        "examples": [
            "Some people you keep. {name} was one of mine.",
            "There are people who feel like home. {name} is mine.",
        ],
    },
    {
        "slug": "question",
        "label": "A question",
        "description": "Open with a question that hooks the viewer.",
        "style": (
            "open with a genuine question that hooks the viewer and the rest of "
            "the film answers"
        ),
        "examples": [
            "How do you sum up someone like {name}?",
            "You ever meet someone and just know? That was {name}.",
        ],
    },
]

OPENER_PRESET_BY_SLUG: dict[str, dict] = {p["slug"]: p for p in OPENER_PRESETS}


def public_catalog() -> list[dict]:
    """The slug/label/description/examples the CRM dropdown renders (the
    style text is internal prompt wording, but examples make a nice preview)."""
    return [
        {
            "slug": p["slug"],
            "label": p["label"],
            "description": p["description"],
            "examples": list(p["examples"]),
        }
        for p in OPENER_PRESETS
    ]
