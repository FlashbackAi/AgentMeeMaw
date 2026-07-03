"""Template compositor — green-zone detection, panel fills, chapter blends.

Each collection template marks its art zones in pure chroma green. The
compositor finds those zones, wipes them to the template's paper colour, and
composites the Gemini art into them: grid pages get three framed panels;
chapter pages get one large blended illustration with the narration set in
the bottom margin; the cover gets the title + a framed hero illustration.

All zone math is spike-validated. Shared blends (tone-match / feather /
autocrop) come from ``flashback.page_render.primitives``.
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from flashback.page_render.primitives import (
    autocrop_content,
    feather_mask,
    tone_match,
)

Box = tuple[int, int, int, int]

_FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tribute_video",
    "assets",
    "fonts",
)
FONT_BODY = os.path.join(_FONT_DIR, "EBGaramond.ttf")
FONT_TITLE = os.path.join(_FONT_DIR, "PlayfairDisplay-Italic.ttf")


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.truetype(FONT_BODY, size)
        except Exception:
            return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if draw.textlength(f"{cur} {w}".strip(), font=font) <= max_w or not cur:
            cur = f"{cur} {w}".strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# --- zones --------------------------------------------------------------------
def green_components(path: str) -> tuple[list[Box], tuple[int, int]]:
    """Bounding boxes of the template's chroma-green art zones + (H, W)."""
    arr = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    mask = (arr[..., 1] - np.maximum(arr[..., 0], arr[..., 2])) > 100
    labeled, n = ndimage.label(mask)
    boxes: list[Box] = []
    for lab in range(1, n + 1):
        ys, xs = np.where(labeled == lab)
        if xs.size < 0.008 * mask.size:
            continue
        boxes.append(
            (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        )
    return boxes, mask.shape


def panel_boxes(path: str) -> list[Box]:
    """Green panel rects in reading order (rows top->bottom, cols l->r)."""
    boxes, (H, _W) = green_components(path)
    boxes.sort(key=lambda b: (round((b[1] + b[3]) / 2 / (H * 0.12)), b[0]))
    return boxes or [(0, 0, 1, 1)]


def grid_boxes(template_path: str, n: int) -> list[Box]:
    """Synthesize n stacked panel rects across the template's content region."""
    boxes, (H, W) = green_components(template_path)
    if boxes:
        ux0 = min(b[0] for b in boxes)
        uy0 = min(b[1] for b in boxes)
        ux1 = max(b[2] for b in boxes)
        uy1 = max(b[3] for b in boxes)
    else:
        ux0, uy0 = int(W * 0.08), int(H * 0.08)
        ux1, uy1 = int(W * 0.92), int(H * 0.92)
    gap = int((uy1 - uy0) * 0.035)
    ph = (uy1 - uy0 - gap * (n - 1)) // n
    return [
        (ux0, uy0 + i * (ph + gap), ux1, uy0 + i * (ph + gap) + ph)
        for i in range(n)
    ]


def expand_box(box: Box, W: int, H: int) -> Box:
    """Grow the template art zone (kills the printed frame; bigger picture),
    keeping the template's art aspect, reclaiming the empty space above."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    cx = (x0 + x1) // 2
    tw = int(W * 0.88)
    th = int(tw * bh / bw)
    nx0 = max(int(W * 0.06), cx - tw // 2)
    nx1 = min(int(W * 0.94), nx0 + tw)
    grow = th - bh
    ny0 = max(int(H * 0.11), y0 - int(grow * 0.7))
    ny1 = min(int(H * 0.70), ny0 + th)
    return (nx0, ny0, nx1, ny1)


_ASPECTS = {"16:9": 1.78, "4:3": 1.33, "1:1": 1.0, "3:4": 0.75, "9:16": 0.56}


def gemini_aspect(box: Box) -> str:
    r = (box[2] - box[0]) / max(1, box[3] - box[1])
    return min(_ASPECTS, key=lambda k: abs(_ASPECTS[k] - r))


# --- paper / fills --------------------------------------------------------------
def template_paper_color(template: Image.Image) -> tuple[int, int, int]:
    """Median colour of the template paper (bright, low-saturation pixels).

    Deliberately NOT primitives.paper_color: these templates carry decorative
    borders, so a fixed sample patch is unreliable; the bright/low-sat median
    was validated across all six collections in the spike.
    """
    arr = np.asarray(template.convert("RGB")).astype(np.int16)
    bright = arr.mean(axis=2) > 200
    lowsat = (arr.max(axis=2) - arr.min(axis=2)) < 22
    sel = bright & lowsat
    if int(sel.sum()) > 500:
        return tuple(int(v) for v in np.median(arr[sel].reshape(-1, 3), axis=0))
    w, h = template.size  # fallback: a high patch
    patch = np.asarray(
        template.crop(
            (int(0.40 * w), int(0.13 * h), int(0.60 * w), int(0.18 * h))
        )
    ).reshape(-1, 3)
    return tuple(int(v) for v in np.median(patch, axis=0))


def fit_fill(art: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Cover-crop art to exactly box_w x box_h."""
    aw, ah = art.size
    scale = max(box_w / aw, box_h / ah)
    art = art.resize(
        (max(1, int(aw * scale)), max(1, int(ah * scale))), Image.LANCZOS
    )
    aw, ah = art.size
    left, top = (aw - box_w) // 2, (ah - box_h) // 2
    return art.crop((left, top, left + box_w, top + box_h))


def grid_page_base(template: Image.Image, template_path: str) -> Image.Image:
    """Wipe the template's printed panels/frames to paper, keeping the outer
    decorative border, ready for synthesized panels to be placed on top."""
    paper = template_paper_color(template)
    boxes, _ = green_components(template_path)
    arr = np.asarray(template.convert("RGB")).astype(np.int16)
    if boxes:
        H, W = arr.shape[:2]
        m = int(min(W, H) * 0.015)
        ux0 = max(0, min(b[0] for b in boxes) - m)
        uy0 = max(0, min(b[1] for b in boxes) - m)
        ux1 = min(W, max(b[2] for b in boxes) + m)
        uy1 = min(H, max(b[3] for b in boxes) + m)
        arr[uy0:uy1, ux0:ux1] = paper
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def fill_panel(page: Image.Image, art: Image.Image, box: Box) -> None:
    """Cover-fill the scene into the panel and draw a thin frame."""
    x0, y0, x1, y1 = box
    art = fit_fill(art, x1 - x0, y1 - y0)
    page.paste(art, (x0, y0))
    rad = int(min(x1 - x0, y1 - y0) * 0.04)
    ImageDraw.Draw(page).rounded_rectangle(
        [x0, y0, x1 - 1, y1 - 1], radius=rad, outline=(120, 96, 64), width=3
    )


def blend_chapter(
    template: Image.Image, art: Image.Image, box: Box
) -> Image.Image:
    """Erase the template's printed frame inside ``box`` and melt the scene
    into the paper edge-to-edge (tone-match + feather blend)."""
    paper = template_paper_color(template)
    x0, y0, x1, y1 = box
    arr = np.asarray(template.convert("RGB")).astype(np.int16)
    arr[y0:y1, x0:x1] = paper  # wipe green zone + printed border
    page = Image.fromarray(
        np.clip(arr, 0, 255).astype(np.uint8), "RGB"
    ).convert("RGBA")
    art = autocrop_content(art)
    art = tone_match(art, paper)
    art = fit_fill(art, x1 - x0, y1 - y0).convert("RGBA")
    art.putalpha(feather_mask(art.size, frac=0.07))
    page.alpha_composite(art, (x0, y0))
    return page.convert("RGB")


# --- text ----------------------------------------------------------------------
def overlay_chapter_text(img: Image.Image, box: Box, text: str) -> None:
    """Narration justified to the picture's column width, in the bottom margin."""
    if not text:
        return
    W, H = img.size
    draw = ImageDraw.Draw(img)
    x0, _y0, x1, y1 = box
    col_w = x1 - x0
    top = y1 + int(H * 0.02)
    margin_h = H - top - int(H * 0.05)
    size = max(24, int(W * 0.041))
    font = _font(FONT_TITLE, size)
    lines = _wrap(draw, text, font, col_w)
    lh = int(size * 1.45)
    while lh * len(lines) > margin_h and size > 16:
        size -= 2
        font = _font(FONT_TITLE, size)
        lines = _wrap(draw, text, font, col_w)
        lh = int(size * 1.45)
    fill = (58, 44, 28)
    ty = top
    for idx, ln in enumerate(lines):
        words = ln.split()
        last = idx == len(lines) - 1
        if last or len(words) == 1:  # last / orphan line: center it
            lw = draw.textlength(ln, font=font)
            draw.text((x0 + (col_w - lw) / 2, ty), ln, font=font, fill=fill)
        else:  # full-justify: distribute slack between words
            nat = sum(draw.textlength(w, font=font) for w in words)
            gap = (col_w - nat) / (len(words) - 1)
            x = x0
            for w in words:
                draw.text((x, ty), w, font=font, fill=fill)
                x += draw.textlength(w, font=font) + gap
        ty += lh


def make_cover(
    cover_path: str, title: str, subtitle: str, art: Image.Image | None = None
) -> Image.Image:
    """Title at the top, framed hero illustration, subject name beneath."""
    cover = Image.open(cover_path).convert("RGB")
    W, H = cover.size
    draw = ImageDraw.Draw(cover)
    tsize = int(W * 0.085)
    font = _font(FONT_TITLE, tsize)
    lines = _wrap(draw, title, font, int(W * 0.8))
    y = int(H * 0.10)
    for ln in lines:
        lw = draw.textlength(ln, font=font)
        draw.text(((W - lw) / 2, y), ln, font=font, fill=(58, 44, 28))
        y += int(tsize * 1.2)
    sub_y = y + int(H * 0.012)
    if art is not None:
        box_w = int(W * 0.74)
        top = y + int(H * 0.035)
        box_h = min(int(box_w * 1.2), int(H * 0.82) - top)
        if box_h > int(H * 0.2):
            x0 = (W - box_w) // 2
            framed = fit_fill(art, box_w, box_h)
            rad = int(box_w * 0.045)
            mask = Image.new("L", (box_w, box_h), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, box_w, box_h], radius=rad, fill=255
            )
            cover.paste(framed, (x0, top), mask)
            draw.rounded_rectangle(
                [x0, top, x0 + box_w, top + box_h],
                radius=rad,
                outline=(150, 120, 80),
                width=4,
            )
            sub_y = top + box_h + int(H * 0.022)
    if subtitle:
        sf = _font(FONT_TITLE, int(W * 0.05))
        sw = draw.textlength(subtitle, font=sf)
        draw.text(((W - sw) / 2, sub_y), subtitle, font=sf, fill=(92, 72, 48))
    return cover
