"""Paths, env loading, and the visual constants for the page template.

All layout numbers are FRACTIONS of the template (899x1600), so they hold
if the template is re-exported at a different resolution. Tuned against
reference/example-page.jpg ("His room, his little world.").
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# --- paths ------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
PROTO_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.abspath(os.path.join(PROTO_DIR, "..", ".."))

TEMPLATE_PATH = os.path.join(PROTO_DIR, "templates", "page-template.jpg")
REFERENCE_PATH = os.path.join(PROTO_DIR, "reference", "example-page.jpg")
FONTS_DIR = os.path.join(PROTO_DIR, "fonts")
OUT_DIR = os.path.join(PROTO_DIR, "out")

ENV_PATH = os.path.join(REPO_ROOT, ".env.production")

# override=True: a stale DATABASE_URL=localhost:15432 lives in the shell
# environment; the file must win. (Discovered during bring-up.)
load_dotenv(ENV_PATH, override=True)


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


# --- model selection --------------------------------------------------------
GEMINI_MODEL = "gemini-3.1-flash-image"
STORY_MODEL = env("LLM_BIG_MODEL") or "claude-sonnet-4-6"

DEFAULT_PERSON_ID = "2698fab6-8aef-4aa7-a252-e32c767cc204"  # Chandraiah

# --- fonts ------------------------------------------------------------------
# Main emotional line: Playfair Display Italic (high-contrast display serif,
# matches the reference). Eyebrow: EB Garamond, uppercase + tracked.
MAIN_FONT = os.path.join(FONTS_DIR, "PlayfairDisplay-Italic.ttf")
EYEBROW_FONT = os.path.join(FONTS_DIR, "EBGaramond.ttf")
# Windows fallbacks if a download is ever missing.
MAIN_FONT_FALLBACK = r"C:\Windows\Fonts\georgiai.ttf"
EYEBROW_FONT_FALLBACK = r"C:\Windows\Fonts\constan.ttf"

MAIN_FONT_WEIGHT = 560  # variable-font axis; ~book-bold italic like the ref
MAIN_FONT_FILL = (58, 44, 28)       # #3a2c1c dark sepia ink (matches Node)
EYEBROW_FILL = (150, 118, 72)       # #96764 8 muted brown small-caps
EYEBROW_TRACKING = 0.34             # extra letter spacing, in em


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


# Eyebrow baseline band (unused now headers are removed everywhere; kept so the
# compositor stays generic and headers can be re-enabled).
EYEBROW_BOX = Box(0.12, 0.185, 0.88, 0.225)
# Main 8-10 word line. No header above it, so the box reclaims that space and
# the line sits centered in the upper third. Auto-sized to the largest serif
# that fits.
TEXT_BOX = Box(0.10, 0.205, 0.90, 0.450)
# Illustration zone (lower ~52% of the page). Art is auto-trimmed of blank
# margins, then fit into this large zone, bottom-aligned -- so the painting
# reads BIG, edge to edge.
ART_BOX = Box(0.03, 0.470, 0.97, 0.985)

# Chroma-key green the model paints behind isolated subjects (green blend).
CHROMA_GREEN = (0, 177, 64)  # #00b140

# Aspect ratio requested from Gemini. The big ART_BOX is ~square, so 1:1 fills
# it best -> the painting reads largest. (4:3 left vertical gaps.)
ART_ASPECT = "1:1"

# Auto-detect a user-uploaded prime-years photo for the opener portrait
# (image-to-image). Drop a file at reference/prime_photo.<ext>.
def prime_photo_path() -> str | None:
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = os.path.join(PROTO_DIR, "reference", f"prime_photo.{ext}")
        if os.path.exists(p):
            return p
    return None


# --- per-page layouts -------------------------------------------------------
# Variety across pages on the SAME template. The template's fixed decoration
# (logo, flourish, border) lives at the top/edges, leaving the body free.
@dataclass(frozen=True)
class Layout:
    text_box: Box
    art_box: Box
    art_valign: str  # "top" | "bottom" | "center"


LAYOUTS = {
    # text up top, big picture filling the bottom (the original look)
    "text_top": Layout(Box(0.10, 0.205, 0.90, 0.45),
                       Box(0.03, 0.47, 0.97, 0.985), "bottom"),
    # picture up top (under the flourish), text band at the bottom
    "art_top": Layout(Box(0.10, 0.70, 0.90, 0.93),
                      Box(0.03, 0.15, 0.97, 0.66), "top"),
    # picture is the hero (centered, large); a short text band beneath it
    "art_hero": Layout(Box(0.08, 0.85, 0.92, 0.955),
                       Box(0.02, 0.15, 0.98, 0.83), "center"),
}
_BEAT_CYCLE = ("text_top", "art_top", "text_top", "art_hero", "art_top")


def layout_for(role: str, index: int) -> Layout:
    """Opener/closing keep the calm text-top frame; beats rotate for variety."""
    if role in ("opener", "closing"):
        return LAYOUTS["text_top"]
    return LAYOUTS[_BEAT_CYCLE[index % len(_BEAT_CYCLE)]]
