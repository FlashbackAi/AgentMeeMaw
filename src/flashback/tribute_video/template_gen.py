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

# Lesson from the first live candidates (2026-07-15): describing the text/art
# zones as "bands" made the model PAINT visible rectangular bands and seams
# across the interior. The contract now describes ONE uninterrupted interior
# surface and names those artifacts as hard negatives, and it anchors palette
# discipline so a loud brief ("fun comic style") still composes like a book
# page instead of a poster.
_LAYOUT_CONTRACT = (
    "A decorative PAGE BACKGROUND TEMPLATE for a printed keepsake book, "
    "portrait orientation. Decoration lives ONLY in a border frame hugging "
    "the outer edges and corners (at most the outer tenth of the page). "
    "EVERYTHING inside the frame is one single continuous sheet of plain, "
    "evenly-lit paper in one tone — one uninterrupted surface, as if the "
    "border were drawn around a blank page. STRICTLY FORBIDDEN: visible "
    "rectangles, panels, bands, stripes, seams, tonal steps, boxes, inner "
    "frames, or any second page inside the page; text, lettering, logos, "
    "watermarks; figures, faces, or objects in the interior; drop shadows; "
    "gradients across the interior. The border may be characterful but must "
    "use a harmonious palette of at most three accent colors that would sit "
    "well in a printed storybook — decorative frame art, not a poster. "
    "Hand-painted, print-quality, flat even lighting."
)


def build_template_prompt(brief: str) -> str:
    return (
        f"{_LAYOUT_CONTRACT} Style brief for the border decoration ONLY "
        f"(the interior always stays plain paper): {brief.strip()}"
    )


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
