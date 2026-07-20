"""Assign a layout slug to each scene at render time (spec §8).

Admin control is the Recipe's **palette** (allowed slugs) + **role pins**
(opener / payoff / closing). The code orders the rest: pins win at their
positions; unpinned scenes cycle the palette skipping an immediate repeat.
An empty/unknown palette degrades to ``framed_hero`` — config never blocks a
render (invariant).
"""
from __future__ import annotations

DEFAULT_LAYOUT = "framed_hero"

# The canonical layout library — the agent<->Node contract for the CRM palette
# picker (exposed via GET /flashback/layouts). Slugs must match the Remotion
# registry (remotion/src/layouts/registry.ts). Admins choose from these; they
# never author layouts.
LAYOUT_CATALOG: list[dict] = [
    {"slug": "split_duotone", "label": "Split / Duotone",
     "description": "Art one side, a bold colour block with the beat title the other."},
    {"slug": "scrapbook", "label": "Scrapbook",
     "description": "Overlapping polaroids with a handwritten caption."},
    {"slug": "type_over_crop", "label": "Big Type",
     "description": "Giant kinetic headline over a full-bleed detail crop."},
    {"slug": "fullbleed_caption", "label": "Full-bleed + Caption",
     "description": "Cinematic full frame with a tucked-in corner caption."},
    {"slug": "framed_hero", "label": "Framed (classic)",
     "description": "Calm framed hero with the line above -- the memorial default."},
]

# Motion presets a Recipe can select (spec §9). Forward-looking: the render
# applies one motion style today; the lever becomes live in a later pass.
MOTION_PRESETS: list[str] = ["calm", "playful", "punchy", "cinematic"]

# Structural roles a layout can be pinned to (Recipe role pins).
PINNABLE_ROLES: list[str] = ["opener", "payoff", "closing"]


def assign_layouts(roles: list[str], *, palette: list[str],
                   pins: dict[str, str] | None = None) -> list[str]:
    pins = pins or {}
    pool = list(palette) or [DEFAULT_LAYOUT]
    out: list[str] = []
    prev: str | None = None
    idx = 0
    for role in roles:
        if role in pins:
            slug = pins[role]
        else:
            slug = pool[idx % len(pool)]
            if slug == prev and len(pool) > 1:
                idx += 1
                slug = pool[idx % len(pool)]
            idx += 1
        out.append(slug)
        prev = slug
    return out
