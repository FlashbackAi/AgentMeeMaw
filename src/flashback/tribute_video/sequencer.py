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
    {"slug": "letter_note", "label": "Handwritten letter",
     "description": "The caption inks itself onto letter paper, a photo tucked under tape."},
    {"slug": "filmstrip", "label": "Film strip",
     "description": "A vertical film strip slides through painted frames, caption as the label."},
    {"slug": "postcard", "label": "Postcard",
     "description": "The scene lands as a tilted vintage postcard with stamp and postmark."},
    {"slug": "word_mask", "label": "Word mask",
     "description": "The art shows through one giant word, the caption settling beneath."},
    {"slug": "torn_reveal", "label": "Torn paper",
     "description": "Paper tears apart to reveal the scene full-bleed between the layers."},
    {"slug": "gallery_wall", "label": "Gallery wall",
     "description": "Framed paintings on a quiet wall with a brass caption plaque."},
    {"slug": "magazine", "label": "Editorial",
     "description": "A clean magazine spread: tall art, vertical eyebrow, serif headline."},
    {"slug": "map_journey", "label": "Journey map",
     "description": "A dotted route draws across parchment to a pinned photo, script caption."},
]

# Layouts that paint two images per scene; the props builder attaches a
# distinct ``image2`` for these (falls back to ``image`` when the render
# only produced one still).
MULTI_IMAGE_LAYOUTS: set[str] = {"scrapbook", "filmstrip", "gallery_wall"}

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
