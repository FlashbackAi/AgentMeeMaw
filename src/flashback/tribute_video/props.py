"""Build the props JSON the Remotion project consumes from a Book + StyleKit.

Pure data assembly — no I/O. Mirrors the page order the legacy renderer builds
(opener, beats..., message?, closing). The last beat is tagged ``payoff`` so a
Recipe's payoff role-pin lands on the emotional peak. ``image_names`` maps each
scene to a PNG the caller wrote into the Remotion public dir; the message scene
reuses the opener image as a bookend, and multi-image scenes (scrapbook,
filmstrip, gallery_wall) get a distinct second image so the panels differ.
"""
from __future__ import annotations

from .book import Book
from .sequencer import MULTI_IMAGE_LAYOUTS, assign_layouts
from .style import StyleKit

WIDTH, HEIGHT = 896, 1600

# Font filename-stem (substring) -> CSS family. The StyleKit carries font FILE
# paths (Pillow needs paths); Remotion needs family names, so we map by stem.
_FONT_FAMILY_BY_STEM: tuple[tuple[str, str], ...] = (
    ("playfair", "Playfair Display"),
    ("garamond", "EB Garamond"),
    ("caveat", "Caveat"),
    ("nunito", "Nunito"),
)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _family_for(font_path: str, default: str) -> str:
    stem = font_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    for needle, family in _FONT_FAMILY_BY_STEM:
        if needle in stem:
            return family
    return default


_DISPLAY_SKIP_WORDS = {
    "the", "a", "an", "she", "he", "they", "we", "i", "it", "her", "his",
    "their", "our", "my", "and", "of", "in", "on", "at", "was", "is",
}


def derive_display(line: str) -> str:
    """2-4 word typographic title from a page line (LLM `display` fallback).

    Skips weak leading words so the first word survives rendering alone at
    giant size (word_mask), then takes up to 3 strong words, Title Case.
    """
    words = [w.strip(".,;:!?\"'()") for w in (line or "").split()]
    words = [w for w in words if w]
    strong = [w for w in words if w.lower() not in _DISPLAY_SKIP_WORDS]
    picked = (strong or words)[:3]
    return " ".join(w.capitalize() for w in picked)


def build_props(book: Book, *, kit: StyleKit, image_names: dict[str, str],
                palette: list[str], pins: dict[str, str] | None = None,
                fps: int = 30, hold: float = 2.4, transition: float = 0.7,
                accent: str = "#e8552e", motion_preset: str = "") -> dict:
    # (role, image_key, text, display) in book order; the final beat is the
    # payoff. `display` is the 2-4 word form typographic layouts render.
    def entry(role: str, key: str, beat_line: str, beat_display: str = ""):
        return (role, key, beat_line, beat_display or derive_display(beat_line))

    items = [entry("opener", "opener", book.opener.line, book.opener.display)]
    n = len(book.beats)
    for i, b in enumerate(book.beats):
        role = "payoff" if (n > 0 and i == n - 1) else "beat"
        items.append(entry(role, f"beat_{i}", b.line, b.display))
    if book.message.strip():
        items.append(entry("message", "opener", book.message))
    items.append(entry("closing", "closing", book.closing.line, book.closing.display))

    layouts = assign_layouts([r for r, _, _, _ in items], palette=palette, pins=pins)
    all_images = list(image_names.values())

    scenes: list[dict] = []
    for (role, image_key, text, display), layout in zip(items, layouts):
        image = image_names[image_key]
        scene: dict = {"role": role, "layout_slug": layout, "text": text,
                       "display": display, "image": image}
        if layout in MULTI_IMAGE_LAYOUTS:
            scene["image2"] = next((x for x in all_images if x != image), image)
        scenes.append(scene)

    return {
        "meta": {"width": WIDTH, "height": HEIGHT, "fps": fps,
                 "cover_title": book.cover_title},
        "recipe": {
            "fonts": {
                "main_family": _family_for(kit.main_font, "Playfair Display"),
                "eyebrow_family": _family_for(kit.eyebrow_font, "EB Garamond"),
                "display_family": "Nunito",
                "script_family": "Caveat",
            },
            "ink": {"main_fill": _rgb_to_hex(kit.main_fill),
                    "eyebrow_fill": _rgb_to_hex(kit.eyebrow_fill),
                    "accent": accent},
            "pacing": {"hold": hold, "transition": transition},
            "motion_preset": motion_preset,
        },
        "scenes": scenes,
    }
