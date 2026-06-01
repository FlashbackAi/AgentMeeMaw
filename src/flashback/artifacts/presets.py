"""Stylistic presets for image / video artifact regeneration.

Presets give the user a small, named set of stylistic variations on the
default RDR2 painterly-realism register. The default preset is a no-op:
it returns the base prompt unchanged so legacies look consistent unless
the user actively picks a variant.

Slugs are part of the agent ↔ Node contract and must stay stable. The
``label`` / ``description`` fields are user-facing and can be tuned
freely. Modifier strings are appended to the composed prompt; keep them
short and additive — they should refine, not replace, the base look.
"""

from __future__ import annotations

from typing import TypedDict


class Preset(TypedDict):
    slug: str
    label: str
    description: str
    is_default: bool
    modifier: str


DEFAULT_PRESET_SLUG = "painterly_cinematic"

_PRESETS: dict[str, Preset] = {
    "painterly_cinematic": {
        "slug": "painterly_cinematic",
        "label": "Painterly cinematic",
        "description": (
            "The default Flashback look — RDR2-style painterly realism "
            "with soft cinematic lighting."
        ),
        "is_default": True,
        "modifier": "",
    },
    "golden_hour": {
        "slug": "golden_hour",
        "label": "Golden hour",
        "description": "Warm late-afternoon light with long soft shadows.",
        "is_default": False,
        "modifier": (
            "lit by warm late-afternoon golden-hour light, long soft "
            "shadows, honey-amber color cast"
        ),
    },
    "twilight": {
        "slug": "twilight",
        "label": "Twilight",
        "description": "Cool blue-hour light, rich shadows, warm window glow.",
        "is_default": False,
        "modifier": (
            "set at twilight, cool blue-hour ambient light with rich "
            "indigo shadows, warm interior window glow, high tonal contrast"
        ),
    },
    "storybook": {
        "slug": "storybook",
        "label": "Storybook",
        "description": "Softer painterly brushwork, gentle storybook warmth.",
        "is_default": False,
        "modifier": (
            "softer painterly brushwork with slightly stylized proportions, "
            "gentle storybook warmth, less hard edge detail"
        ),
    },
    "vintage_film": {
        "slug": "vintage_film",
        "label": "Vintage film",
        "description": "Subtle film grain, faded color, 70s photochrome palette.",
        "is_default": False,
        "modifier": (
            "rendered with subtle vintage film grain, slightly faded color, "
            "soft vignette, 70s photochrome palette"
        ),
    },
}


_PUBLIC_FIELDS = ("slug", "label", "description", "is_default")


def list_presets() -> list[dict]:
    """Return the public preset list, ordered with the default first.

    Modifier strings stay internal — they leak prompt-engineering detail
    that clients don't need. Only the user-facing fields are exposed.
    """
    default = [_PRESETS[DEFAULT_PRESET_SLUG]]
    others = [p for slug, p in _PRESETS.items() if slug != DEFAULT_PRESET_SLUG]
    return [{k: p[k] for k in _PUBLIC_FIELDS} for p in [*default, *others]]


def resolve_preset(slug: str | None) -> str:
    """Validate a slug, returning it normalized. ``None`` resolves to default."""
    if slug is None:
        return DEFAULT_PRESET_SLUG
    if slug not in _PRESETS:
        raise ValueError(f"unknown preset slug: {slug!r}")
    return slug


def apply_preset(prompt: str, slug: str | None) -> str:
    """Append the preset's stylistic modifier to ``prompt``.

    ``slug=None`` resolves to the default (no-op). Raises ``ValueError`` on
    unknown slug so callers fail loudly rather than silently degrade.
    """
    resolved = resolve_preset(slug)
    modifier = _PRESETS[resolved]["modifier"]
    if not modifier:
        return prompt
    base = prompt.rstrip(", ").rstrip()
    return f"{base}, {modifier}" if base else modifier
