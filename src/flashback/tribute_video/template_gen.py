"""Page-template generation for the tribute CRM (spec 2026-07-14 §2.6).

The template is the page BACKGROUND the compositor lays text + art onto, so
the generation prompt embeds the layout contract as a hard constraint: the
text band and the art band must stay calm and low-texture or the composited
page becomes illegible. The CRM shows candidates + a real composited sample
page before anything publishes — the human eye is the final gate.
"""

from __future__ import annotations

import io

from PIL import Image

from flashback.page_render.art import Artist

# 899x1600 is the shipped template's geometry; the fractional layout Boxes in
# style.py hold at any resolution with this aspect.
TEMPLATE_ASPECT = "9:16"
MAX_TEMPLATE_BYTES = 2 * 1024 * 1024  # spec §3.3 size cap

_LAYOUT_CONTRACT = (
    "A decorative PAGE BACKGROUND TEMPLATE for a printed keepsake book, "
    "portrait orientation. Ornamentation lives ONLY at the outer border and "
    "corners. The horizontal band from 18% to 46% of the page height and the "
    "band from 47% to 98% must stay calm, low-texture and near-uniform paper "
    "so printed text and a pasted illustration stay legible. Absolutely no "
    "text, no lettering, no figures, no faces, no objects in the middle of "
    "the page. Painterly, print-quality, flat lighting."
)


def build_template_prompt(brief: str) -> str:
    return f"{_LAYOUT_CONTRACT} Style brief: {brief.strip()}"


def _encode_jpeg_capped(img: Image.Image) -> bytes:
    """JPEG bytes under the size cap, stepping quality down as needed."""
    quality = 88
    while True:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= MAX_TEMPLATE_BYTES or quality <= 40:
            return data
        quality -= 8


def generate_template_candidates(
    artist: Artist, *, brief: str, n: int
) -> list[bytes]:
    """Up to ``n`` (<=4) candidate template JPEGs. Sequential — candidate
    count is tiny and the admin surface is rate-limited anyway."""
    out: list[bytes] = []
    prompt = build_template_prompt(brief)
    for _ in range(max(1, min(int(n), 4))):
        img = artist.raw(prompt, TEMPLATE_ASPECT)
        out.append(_encode_jpeg_capped(img))
    return out
