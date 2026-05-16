"""Canonical universal-theme catalog.

Five themes are seeded for every legacy at person-creation time. They start
in ``state='locked'`` and progress to ``unlocked`` only when the user runs
the archetype-question unlock flow. The catalog is intentionally narrow —
emergent themes (e.g. 'Cricket', 'Gardening') cover anything that isn't
universal across lives.

Slug stability matters: the slug is part of the unique active-slug index
and is referenced by code paths (extraction tagging, archetype prompts).
Display names are user-facing and can be tweaked freely; slugs cannot.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniversalTheme:
    slug: str
    display_name: str
    description: str
    """A short, human-readable scope hint used by the extraction-tagging
    prompt and the archetype-question generator to ground the LLM in
    what this theme covers."""


UNIVERSAL_THEMES: tuple[UniversalTheme, ...] = (
    UniversalTheme(
        slug="family",
        display_name="Family",
        description=(
            "Moments with parents, siblings, partners, children, "
            "grandparents, extended family, in-laws, and chosen family. "
            "Anything where a family relationship is central."
        ),
    ),
    UniversalTheme(
        slug="career",
        display_name="Career",
        description=(
            "Work, profession, training, school, calling, craft, or "
            "vocation — anything the subject did with their time as their "
            "work in the world."
        ),
    ),
    UniversalTheme(
        slug="friendships",
        display_name="Friendships",
        description=(
            "Friends, mentors, mentees, colleagues, classmates, neighbors, "
            "community — close non-family bonds the subject built or "
            "carried."
        ),
    ),
    UniversalTheme(
        slug="beliefs",
        display_name="Beliefs & Values",
        description=(
            "Faith, worldview, principles, ethics, politics, philosophy, "
            "what mattered to the subject and why — moments that reveal "
            "what they stood for."
        ),
    ),
    UniversalTheme(
        slug="milestones",
        display_name="Milestones",
        description=(
            "Weddings, births, graduations, deaths, moves, retirements, "
            "first jobs, big achievements, life-changing events — the "
            "moments a life is often remembered by."
        ),
    ),
)


UNIVERSAL_THEME_SLUGS: frozenset[str] = frozenset(t.slug for t in UNIVERSAL_THEMES)


def get_universal_theme(slug: str) -> UniversalTheme | None:
    for theme in UNIVERSAL_THEMES:
        if theme.slug == slug:
            return theme
    return None
