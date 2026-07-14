"""Visual constants for the page template: layout fractions, colours, fonts.

All layout numbers are FRACTIONS of the template (899x1600), so they hold if the
template is re-exported at a different resolution. Assets ship as package data;
the Gemini model id + API key are passed in from settings (never read here).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# --- assets (package data) --------------------------------------------------
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
TEMPLATE_PATH = os.path.join(ASSETS_DIR, "page-template.jpg")
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")
AUDIO_DIR = os.path.join(ASSETS_DIR, "audio")
# Default backing music: a soft sentimental piano bed under the storybook.
AUDIO_PATH = os.path.join(AUDIO_DIR, "sentimental-piano.mp3")

# Main emotional line: Playfair Display Italic (high-contrast display serif).
MAIN_FONT = os.path.join(FONTS_DIR, "PlayfairDisplay-Italic.ttf")
EYEBROW_FONT = os.path.join(FONTS_DIR, "EBGaramond.ttf")
MAIN_FONT_FALLBACK = r"C:\Windows\Fonts\georgiai.ttf"
EYEBROW_FONT_FALLBACK = r"C:\Windows\Fonts\constan.ttf"

MAIN_FONT_WEIGHT = 560              # variable-font axis; ~book-bold italic
MAIN_FONT_FILL = (58, 44, 28)      # #3a2c1c dark sepia ink
EYEBROW_FILL = (150, 118, 72)      # muted brown small-caps (headers off by default)
EYEBROW_TRACKING = 0.34            # extra letter spacing, in em

# Chroma-key green for the "green" blend (subject isolated on solid green).
CHROMA_GREEN = (0, 177, 64)        # #00b140
# Aspect requested from Gemini; the big ART_BOX is ~square so 1:1 fills it best.
ART_ASPECT = "1:1"


@dataclass(frozen=True)
class Box:
    """Fractional rectangle on the page (0..1)."""
    x0: float
    y0: float
    x1: float
    y1: float

    def px(self, w: int, h: int) -> tuple[int, int, int, int]:
        return (round(self.x0 * w), round(self.y0 * h),
                round(self.x1 * w), round(self.y1 * h))


# --- configurable style kit (tribute CRM, spec 2026-07-14) -------------------
# The compositor/renderer consume a StyleKit instead of module constants so a
# visual theme (generated page template + fonts + inks + track) can restyle a
# render without code. DEFAULT_KIT is the shipped Father's Day look; the
# registries are the curated libraries the CRM picks from — expanding them is
# a content task: drop the file in assets/ and add one entry.


@dataclass(frozen=True)
class StyleKit:
    template_path: str = TEMPLATE_PATH
    main_font: str = MAIN_FONT
    eyebrow_font: str = EYEBROW_FONT
    main_font_weight: int = MAIN_FONT_WEIGHT
    main_fill: tuple[int, int, int] = MAIN_FONT_FILL
    eyebrow_fill: tuple[int, int, int] = EYEBROW_FILL
    audio_path: str = AUDIO_PATH


DEFAULT_KIT = StyleKit()

FONT_REGISTRY: dict[str, str] = {
    "playfair_italic": MAIN_FONT,
    "eb_garamond": EYEBROW_FONT,
}
AUDIO_REGISTRY: dict[str, str] = {
    "sentimental_piano": AUDIO_PATH,
}


def _hex_to_rgb(value: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    s = (value or "").strip().lstrip("#")
    if len(s) != 6:
        return default
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return default


def kit_from_style_dict(
    style: dict | None, *, template_override_path: str | None = None
) -> StyleKit:
    """A render-context ``style`` dict as a StyleKit.

    Unknown slugs and missing fields fall back to DEFAULT_KIT members — a
    render never blocks on config (spec section 6.5). ``template_override_path``
    (the worker's tmp file holding DB template bytes) wins over the built-in.
    """
    if style is None and template_override_path is None:
        return DEFAULT_KIT
    style = style or {}
    fonts = style.get("fonts") or {}
    ink = style.get("ink") or {}
    return StyleKit(
        template_path=template_override_path or TEMPLATE_PATH,
        main_font=FONT_REGISTRY.get(fonts.get("main_slug"), MAIN_FONT),
        eyebrow_font=FONT_REGISTRY.get(fonts.get("eyebrow_slug"), EYEBROW_FONT),
        main_font_weight=MAIN_FONT_WEIGHT,
        main_fill=_hex_to_rgb(ink.get("main_fill", ""), MAIN_FONT_FILL),
        eyebrow_fill=_hex_to_rgb(ink.get("eyebrow_fill", ""), EYEBROW_FILL),
        audio_path=AUDIO_REGISTRY.get(style.get("audio_slug"), AUDIO_PATH),
    )


# Header band — unused while headers are off, kept so the compositor stays generic.
EYEBROW_BOX = Box(0.12, 0.185, 0.88, 0.225)
# Default boxes (the "text_top" layout).
TEXT_BOX = Box(0.10, 0.205, 0.90, 0.450)
ART_BOX = Box(0.03, 0.470, 0.97, 0.985)


# --- per-page layouts -------------------------------------------------------
@dataclass(frozen=True)
class Layout:
    text_box: Box
    art_box: Box
    art_valign: str  # "top" | "bottom" | "center"


LAYOUTS = {
    "text_top": Layout(Box(0.10, 0.205, 0.90, 0.45),
                       Box(0.03, 0.47, 0.97, 0.985), "bottom"),
    "art_top": Layout(Box(0.10, 0.70, 0.90, 0.93),
                      Box(0.03, 0.15, 0.97, 0.66), "top"),
    "art_hero": Layout(Box(0.08, 0.85, 0.92, 0.955),
                       Box(0.02, 0.15, 0.98, 0.83), "center"),
}
_BEAT_CYCLE = ("text_top", "art_top", "text_top", "art_hero", "art_top")


def layout_for(role: str, index: int) -> Layout:
    """Opener/closing/message keep the calm text-top frame; beats rotate."""
    if role in ("opener", "closing", "message"):
        return LAYOUTS["text_top"]
    return LAYOUTS[_BEAT_CYCLE[index % len(_BEAT_CYCLE)]]
