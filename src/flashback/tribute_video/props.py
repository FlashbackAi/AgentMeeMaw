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


# Layouts that render only the 2-4 word `display` title. The contributor's own
# message must never land on one -- it would be reduced to three words and the
# text they actually wrote would never appear in the video.
DISPLAY_ONLY_LAYOUTS: set[str] = {
    "split_duotone", "type_over_crop", "word_mask", "scrapbook", "filmstrip",
}
# Preference order for the message page; first one present in the palette wins.
MESSAGE_LAYOUTS: tuple[str, ...] = (
    "letter_note", "fullbleed_caption", "framed_hero", "torn_reveal", "magazine",
)

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


def _neighbour_image(page_images: list[str], index: int, *, cover: str) -> str:
    """The second panel for a two-image layout: art from an ADJACENT page.

    It used to be "the first image that isn't this one", which resolved to the
    cover portrait on every page — so a scrapbook, a filmstrip and a gallery
    wall in the same deck each paired their scene with the same face, next to
    text about something else entirely. A neighbour keeps both panels in the
    same stretch of the story, and the cover is excluded outright: it is the
    one page that reproduces a real likeness, not a memory.
    """
    own = page_images[index]
    for offset in (1, -1, 2, -2, 3, -3):
        j = index + offset
        if 0 <= j < len(page_images):
            candidate = page_images[j]
            if candidate != own and candidate != cover:
                return candidate
    # Thin deck: any distinct image beats repeating the panel, cover included.
    return next((x for x in page_images if x != own), own)


def _rehome_message(items: list, layouts: list[str], *,
                    palette: list[str]) -> list[str]:
    """Move the message page off a display-only layout, in place of a caption one.

    ``assign_layouts`` cycles the palette by position and knows nothing about
    what a layout renders; when the message landed on e.g. ``split_duotone`` the
    contributor's paragraph was replaced by a three-word title. Prefer the
    palette's own caption layout so an admin's palette choice still holds; fall
    back to ``letter_note`` (the message register) if the palette has none.
    """
    out = list(layouts)
    for i, (role, _key, _text, _display) in enumerate(items):
        if role != "message" or out[i] not in DISPLAY_ONLY_LAYOUTS:
            continue
        pool = set(palette)
        out[i] = next((s for s in MESSAGE_LAYOUTS if s in pool), MESSAGE_LAYOUTS[0])
    return out


def build_props(book: Book, *, kit: StyleKit, image_names: dict[str, str],
                palette: list[str], pins: dict[str, str] | None = None,
                fps: int = 30, hold: float = 2.4, transition: float = 0.7,
                accent: str = "#e8552e", motion_preset: str = "",
                labels: dict[str, str] | None = None) -> dict:
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
        # No `display` for the message: caption layouts render the full text,
        # and a distilled title would silently replace what the user wrote.
        items.append(("message", "opener", book.message, ""))
    items.append(entry("closing", "closing", book.closing.line, book.closing.display))

    layouts = assign_layouts([r for r, _, _, _ in items], palette=palette, pins=pins)
    layouts = _rehome_message(items, layouts, palette=palette)
    page_images = [image_names[key] for _r, key, _t, _d in items]
    cover = image_names.get("opener", "")

    scenes: list[dict] = []
    for i, ((role, image_key, text, display), layout) in enumerate(zip(items, layouts)):
        image = image_names[image_key]
        scene: dict = {"role": role, "layout_slug": layout, "text": text,
                       "display": display, "image": image}
        if layout in MULTI_IMAGE_LAYOUTS:
            scene["image2"] = _neighbour_image(page_images, i, cover=cover)
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
            # Chrome the layouts used to hard-code. The old literals were
            # memorial-specific ("A LIFE REMEMBERED" on a Friendship-Day page)
            # and repeated ("CHAPTER ONE" on every split_duotone in a 16-page
            # deck); the tribute's own cover title is right for any occasion.
            "labels": _labels(book, labels),
        },
        "scenes": scenes,
    }


def _labels(book: Book, override: dict[str, str] | None) -> dict[str, str]:
    title = (book.cover_title or "").strip()
    out = {"chapter": title, "editorial": title, "stamp": "with love"}
    for key, value in (override or {}).items():
        if key in out and isinstance(value, str):
            out[key] = value.strip()
    return out
