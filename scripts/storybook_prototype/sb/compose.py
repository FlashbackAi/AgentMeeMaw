"""Pillow compositor: lay a generated illustration + the 6-8 word line into the
fixed page template so they read as one printed page.

Two blend modes for the illustration:
  * "cream"  -> art is painted on warm paper; tone-match its paper to the
                template's and feather the rectangle edges so it melts in.
  * "green"  -> art is painted on chroma-green; key the green to alpha so the
                subject floats directly on the template paper (mirrors Node).
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import config
from .config import ART_BOX, Box, EYEBROW_BOX, LAYOUTS, TEXT_BOX


# --- template ---------------------------------------------------------------
def load_template() -> Image.Image:
    return Image.open(config.TEMPLATE_PATH).convert("RGB")


def paper_color(page: Image.Image) -> tuple[int, int, int]:
    """Median colour of a blank patch in the upper-middle of the page."""
    w, h = page.size
    patch = page.convert("RGB").crop(
        (int(0.40 * w), int(0.30 * h), int(0.60 * w), int(0.36 * h))
    )
    arr = np.asarray(patch).reshape(-1, 3)
    return tuple(int(v) for v in np.median(arr, axis=0))


# --- fonts ------------------------------------------------------------------
def _font(path: str, size: int, fallback: str, weight: int | None = None):
    try:
        f = ImageFont.truetype(path, size)
    except Exception:
        f = ImageFont.truetype(fallback, size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
    return f


def _text_w(font, s: str) -> int:
    return int(font.getbbox(s)[2] - font.getbbox(s)[0])


def _wrap(words: list[str], font, max_w: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if _text_w(font, trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit_main(text: str, max_w: int, max_h: int,
              hi: int = 86, lo: int = 30, line_gap: float = 1.18):
    """Largest Playfair-italic size whose wrapped block fits the box."""
    words = text.split()
    best = None
    for size in range(hi, lo - 1, -2):
        font = _font(config.MAIN_FONT, size, config.MAIN_FONT_FALLBACK,
                     config.MAIN_FONT_WEIGHT)
        lines = _wrap(words, font, max_w)
        asc, desc = font.getmetrics()
        block_h = int((asc + desc) * line_gap * len(lines))
        widest = max((_text_w(font, ln) for ln in lines), default=0)
        if block_h <= max_h and widest <= max_w:
            best = (font, lines, asc, desc, line_gap)
            break
    if best is None:
        font = _font(config.MAIN_FONT, lo, config.MAIN_FONT_FALLBACK,
                     config.MAIN_FONT_WEIGHT)
        asc, desc = font.getmetrics()
        best = (font, _wrap(words, font, max_w), asc, desc, line_gap)
    return best


def draw_main_line(draw: ImageDraw.ImageDraw, text: str, box: Box, size_px):
    w, h = size_px
    x0, y0, x1, y1 = box.px(w, h)
    bw, bh = x1 - x0, y1 - y0
    font, lines, asc, desc, line_gap = _fit_main(text, bw, bh)
    line_h = int((asc + desc) * line_gap)
    block_h = line_h * len(lines)
    cy = y0 + (bh - block_h) // 2
    cx = (x0 + x1) // 2
    for i, ln in enumerate(lines):
        lw = _text_w(font, ln)
        draw.text((cx - lw // 2, cy + i * line_h), ln,
                  font=font, fill=config.MAIN_FONT_FILL)


def draw_eyebrow(draw: ImageDraw.ImageDraw, text: str, box: Box, size_px):
    if not text:
        return
    w, h = size_px
    x0, y0, x1, y1 = box.px(w, h)
    bw, bh = x1 - x0, y1 - y0
    text = text.upper()
    size = max(14, int(bh * 0.62))
    font = _font(config.EYEBROW_FONT, size, config.EYEBROW_FONT_FALLBACK)
    track = int(size * config.EYEBROW_TRACKING)
    # total width with tracking between chars
    widths = [_text_w(font, c if c != " " else " ") for c in text]
    total = sum(widths) + track * (len(text) - 1)
    cx = (x0 + x1) // 2
    sx = cx - total // 2
    asc, desc = font.getmetrics()
    cy = y0 + (bh - (asc + desc)) // 2
    x = sx
    for c, cw in zip(text, widths):
        draw.text((x, cy), c, font=font, fill=config.EYEBROW_FILL)
        x += cw + track


# --- illustration blends ----------------------------------------------------
def chroma_key_green(img: Image.Image) -> Image.Image:
    """Key out chroma-green -> RGBA with the subject isolated (despilled)."""
    arr = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    greenness = g - np.maximum(r, b)
    # alpha: 0 where strongly green, 255 where not; soft band in between.
    alpha = np.clip((45 - greenness) / 35.0, 0.0, 1.0)
    alpha = (alpha * 255).astype(np.uint8)
    # despill: pull green down toward the next-brightest channel on edges.
    g2 = g.copy()
    spill = greenness > 0
    g2[spill] = np.maximum(r, b)[spill]
    rgba = np.dstack([r, g2, b, alpha]).astype(np.uint8)
    out = Image.fromarray(rgba, "RGBA")
    a = out.getchannel("A").filter(ImageFilter.GaussianBlur(1.2))
    out.putalpha(a)
    return out


def _tone_match(art: Image.Image, paper: tuple[int, int, int]) -> Image.Image:
    """Shift the art so its (bright) paper background equals the template's."""
    arr = np.asarray(art.convert("RGB")).astype(np.float32)
    lum = arr.mean(axis=2)
    thr = np.percentile(lum, 80)
    bg = arr[lum >= thr].reshape(-1, 3).mean(axis=0)
    delta = np.asarray(paper, dtype=np.float32) - bg
    out = np.clip(arr + delta, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB")


def _feather_mask(size: tuple[int, int], frac: float = 0.06) -> Image.Image:
    w, h = size
    fx, fy = max(2, int(w * frac)), max(2, int(h * frac))
    rx = np.ones(w, np.float32)
    rx[:fx] = np.linspace(0, 1, fx)
    rx[-fx:] = np.linspace(1, 0, fx)
    ry = np.ones(h, np.float32)
    ry[:fy] = np.linspace(0, 1, fy)
    ry[-fy:] = np.linspace(1, 0, fy)
    mask = np.clip(np.minimum.outer(ry, rx) * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(mask, "L")


def _autocrop_content(img: Image.Image, thresh: int = 24,
                      pad_frac: float = 0.015) -> Image.Image:
    """Trim the blank paper margin around the painted content so the picture
    reads big in the zone. Background estimated from the image corners."""
    arr = np.asarray(img.convert("RGB")).astype(np.int16)
    h, w = arr.shape[:2]
    c = max(4, int(min(h, w) * 0.03))
    corners = np.concatenate([
        arr[:c, :c].reshape(-1, 3), arr[:c, -c:].reshape(-1, 3),
        arr[-c:, :c].reshape(-1, 3), arr[-c:, -c:].reshape(-1, 3),
    ])
    bg = np.median(corners, axis=0)
    diff = np.abs(arr - bg).sum(axis=2)
    mask = diff > thresh
    if not mask.any():
        return img
    ys, xs = np.where(mask)
    pad = int(min(h, w) * pad_frac)
    y0, y1 = max(0, int(ys.min()) - pad), min(h, int(ys.max()) + pad)
    x0, x1 = max(0, int(xs.min()) - pad), min(w, int(xs.max()) + pad)
    return img.crop((x0, y0, x1, y1))


def place_illustration(page: Image.Image, illo: Image.Image, box: Box,
                       blend: str, paper: tuple[int, int, int],
                       valign: str = "bottom") -> None:
    """Composite the illustration into ``box`` (centered horizontally)."""
    w, h = page.size
    x0, y0, x1, y1 = box.px(w, h)
    zw, zh = x1 - x0, y1 - y0
    if blend == "green":
        art = chroma_key_green(illo)
        bbox = art.getchannel("A").getbbox()  # trim to the keyed subject
        if bbox:
            art = art.crop(bbox)
    else:
        art = _autocrop_content(illo)          # trim blank paper margin
        art = _tone_match(art, paper)
        art = art.convert("RGBA")
        art.putalpha(_feather_mask(art.size))
    art = ImageOps.contain(art, (zw, zh), Image.LANCZOS)
    ax = x0 + (zw - art.width) // 2
    if valign == "top":
        ay = y0
    elif valign == "center":
        ay = y0 + (zh - art.height) // 2
    else:
        ay = y1 - art.height  # bottom-align
    page.alpha_composite(art, (ax, ay))


# --- layers + page ----------------------------------------------------------
def illustration_layer(template: Image.Image, illo: Image.Image | None,
                       blend: str = "cream", box: Box | None = None,
                       valign: str = "bottom") -> Image.Image:
    """Transparent full-page RGBA carrying only the placed illustration."""
    layer = Image.new("RGBA", template.size, (0, 0, 0, 0))
    if illo is not None:
        place_illustration(layer, illo, box or ART_BOX, blend,
                           paper_color(template), valign)
    return layer


def text_layer(template: Image.Image, eyebrow: str, line: str,
               box: Box | None = None) -> Image.Image:
    """Transparent full-page RGBA carrying only the eyebrow + main line."""
    layer = Image.new("RGBA", template.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw_eyebrow(draw, eyebrow, EYEBROW_BOX, template.size)
    draw_main_line(draw, line, box or TEXT_BOX, template.size)
    return layer


def compose_page(*, eyebrow: str, line: str, illo: Image.Image | None,
                 blend: str = "cream", template: Image.Image | None = None,
                 layout=None) -> Image.Image:
    base = (template or load_template())
    lay = layout or LAYOUTS["text_top"]
    page = base.convert("RGBA")
    page = Image.alpha_composite(
        page, illustration_layer(base, illo, blend, lay.art_box, lay.art_valign))
    page = Image.alpha_composite(page, text_layer(base, eyebrow, line, lay.text_box))
    return page.convert("RGB")
