"""Emotional-tag registry for storybooks.

A storybook carries 1-3 emotional tags from this fixed, code-side vocabulary.
The Sonnet assembler picks the slugs that best fit the chosen moments (and
tones the captions to match); Node maps the *stable slugs* to render templates,
so the slug set is part of the agent <-> Node contract -- add slugs, don't
rename them. Display names are user-facing and may be tweaked freely.

This mirrors the universal-themes / ground-truth registry pattern: the LLM
chooses from the catalog rendered into its prompt, and persistence drops any
slug the LLM invents that isn't in the registry (invariant #6, under-extract).
"""

from __future__ import annotations

# Ordered (slug, display_name). Order is display order for any UI that lists
# the registry; the first entry is also the conventional fallback.
STORYBOOK_TAGS: tuple[tuple[str, str], ...] = (
    ("warmth", "Warmth"),
    ("happiness", "Happiness"),
    ("nostalgia", "Nostalgia"),
    ("love", "Love"),
    ("pride", "Pride"),
    ("gratitude", "Gratitude"),
    ("resilience", "Resilience"),
    ("adventure", "Adventure"),
    ("mischief", "Mischief"),
    ("wonder", "Wonder"),
    ("longing", "Longing"),
    ("grief", "Grief"),
    ("peace", "Peace"),
)

# Cap on how many tags a single storybook carries (assembler is asked for 1-3).
MAX_STORYBOOK_TAGS = 3

_SLUGS: frozenset[str] = frozenset(slug for slug, _ in STORYBOOK_TAGS)
_LABEL_BY_SLUG: dict[str, str] = {slug: label for slug, label in STORYBOOK_TAGS}


def is_valid_tag(slug: str) -> bool:
    """True when ``slug`` is a known registry tag."""
    return slug in _SLUGS


def normalize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
    """Lower-case, validate against the registry, de-dupe, and cap.

    Unknown slugs are dropped silently (invariant #6). Order of first
    appearance is preserved so the assembler's most-dominant-first ordering
    survives.
    """
    if not tags:
        return []
    out: list[str] = []
    for raw in tags:
        slug = (raw or "").strip().lower()
        if slug in _SLUGS and slug not in out:
            out.append(slug)
        if len(out) >= MAX_STORYBOOK_TAGS:
            break
    return out


def labels_for(tags: list[str] | tuple[str, ...]) -> list[str]:
    """Display names for a list of (already-normalized) slugs."""
    return [_LABEL_BY_SLUG[s] for s in tags if s in _LABEL_BY_SLUG]


def render_tag_catalog() -> str:
    """Render the registry as an ``<emotional_tags>`` block for the prompt."""
    rows = "\n".join(
        f'  <tag slug="{slug}">{label}</tag>' for slug, label in STORYBOOK_TAGS
    )
    return f"<emotional_tags>\n{rows}\n</emotional_tags>"
