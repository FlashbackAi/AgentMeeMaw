"""Assign a layout slug to each scene at render time (spec §8).

Admin control is the Recipe's **palette** (allowed slugs) + **role pins**
(opener / payoff / closing). The code orders the rest: pins win at their
positions; unpinned scenes cycle the palette skipping an immediate repeat.
An empty/unknown palette degrades to ``framed_hero`` — config never blocks a
render (invariant).
"""
from __future__ import annotations

DEFAULT_LAYOUT = "framed_hero"


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
