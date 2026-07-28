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
