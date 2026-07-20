"""Pillow compositor: lay a generated illustration + the 8-10 word line into the
fixed page template so they read as one printed page.

Two blend modes:
  * "cream"  -> art painted on warm paper; tone-match its paper to the template's
                and feather the edges so it melts in.
  * "green"  -> art painted on chroma-green; key the green to alpha so the subject
                floats on the template paper.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from flashback.page_render.primitives import (
    autocrop_content as _autocrop_content,
    chroma_key_green,
    feather_mask as _feather_mask,
    load_font as _font,
    paper_color,
    text_width as _text_w,
    tone_match as _tone_match,
    wrap_words as _wrap,
)

from . import style
from .style import ART_BOX, Box, EYEBROW_BOX, LAYOUTS, TEXT_BOX


# --- template ---------------------------------------------------------------
def load_template(kit: style.StyleKit | None = None) -> Image.Image:
    return Image.open((kit or style.DEFAULT_KIT).template_path).convert("RGB")


def _fit_main(text: str, max_w: int, max_h: int,
              hi: int = 86, lo: int = 30, line_gap: float = 1.18,
              kit: style.StyleKit | None = None):
    kit = kit or style.DEFAULT_KIT
    words = text.split()
    best = None
    for size in range(hi, lo - 1, -2):
        font = _font(kit.main_font, size, style.MAIN_FONT_FALLBACK,
                     kit.main_font_weight)
        lines = _wrap(words, font, max_w)
        asc, desc = font.getmetrics()
        block_h = int((asc + desc) * line_gap * len(lines))
        widest = max((_text_w(font, ln) for ln in lines), default=0)
        if block_h <= max_h and widest <= max_w:
            best = (font, lines, asc, desc, line_gap)
            break
    if best is None:
        font = _font(kit.main_font, lo, style.MAIN_FONT_FALLBACK,
                     kit.main_font_weight)
        asc, desc = font.getmetrics()
        best = (font, _wrap(words, font, max_w), asc, desc, line_gap)
    return best


def _fits(text: str, max_w: int, max_h: int, hi: int, lo: int,
          kit: style.StyleKit, line_gap: float = 1.18) -> bool:
    """Whether ``text`` fits the box at ANY size in [lo, hi]."""
    words = text.split()
    for size in range(hi, lo - 1, -2):
        font = _font(kit.main_font, size, style.MAIN_FONT_FALLBACK,
                     kit.main_font_weight)
        lines = _wrap(words, font, max_w)
        asc, desc = font.getmetrics()
        if (int((asc + desc) * line_gap * len(lines)) <= max_h
                and max((_text_w(font, ln) for ln in lines), default=0)
                <= max_w):
            return True
    return False


def plan_message_page(template: Image.Image, text: str,
                      layout: "style.Layout",
                      kit: style.StyleKit | None = None
                      ) -> tuple["style.Layout", bool, str]:
    """(layout, include_art, text_to_draw) for the contributor-message page.

    The message is user-authored free text — unlike the 8-10 word beat
    lines it can be a whole paragraph, and the plain fitter used to
    center-overflow it off the page (prod 2026-07-19: the text ran over
    the top border and through the portrait). Escalation:

      1. fits the normal text box (down to 22px)  -> keep the layout + art
      2. fits the box extended over the art zone   -> text-only page
      3. still too long                            -> truncate on a word
                                                      boundary with an
                                                      ellipsis; never spill
    """
    kit = kit or style.DEFAULT_KIT
    w, h = template.size
    tb = layout.text_box
    x0, y0, x1, y1 = tb.px(w, h)
    if _fits(text, x1 - x0, y1 - y0, hi=86, lo=22, kit=kit):
        return layout, True, text
    big = style.Box(tb.x0, tb.y0, tb.x1, 0.88)
    big_layout = style.Layout(big, layout.art_box, layout.art_valign)
    bx0, by0, bx1, by1 = big.px(w, h)
    bw, bh = bx1 - bx0, by1 - by0
    if _fits(text, bw, bh, hi=48, lo=18, kit=kit):
        return big_layout, False, text
    words = text.split()
    while len(words) > 1:
        words = words[:-1]
        cand = " ".join(words).rstrip(",;:.— ") + " …"
        if _fits(cand, bw, bh, hi=18, lo=18, kit=kit):
            return big_layout, False, cand
    return big_layout, False, text


def draw_main_line(draw: ImageDraw.ImageDraw, text: str, box: Box, size_px,
                   kit: style.StyleKit | None = None):
    kit = kit or style.DEFAULT_KIT
    w, h = size_px
    x0, y0, x1, y1 = box.px(w, h)
    bw, bh = x1 - x0, y1 - y0
    font, lines, asc, desc, line_gap = _fit_main(text, bw, bh, kit=kit)
    line_h = int((asc + desc) * line_gap)
    block_h = line_h * len(lines)
    # Never start above the box: an over-tall block (planner miss / direct
    # caller) top-aligns and runs down instead of spilling over the border.
    cy = max(y0, y0 + (bh - block_h) // 2)
    cx = (x0 + x1) // 2
    for i, ln in enumerate(lines):
        lw = _text_w(font, ln)
        draw.text((cx - lw // 2, cy + i * line_h), ln,
                  font=font, fill=kit.main_fill)


def draw_eyebrow(draw: ImageDraw.ImageDraw, text: str, box: Box, size_px,
                 kit: style.StyleKit | None = None):
    if not text:
        return
    kit = kit or style.DEFAULT_KIT
    w, h = size_px
    x0, y0, x1, y1 = box.px(w, h)
    bw, bh = x1 - x0, y1 - y0
    text = text.upper()
    size = max(14, int(bh * 0.62))
    font = _font(kit.eyebrow_font, size, style.EYEBROW_FONT_FALLBACK)
    track = int(size * style.EYEBROW_TRACKING)
    widths = [_text_w(font, c if c != " " else " ") for c in text]
    total = sum(widths) + track * (len(text) - 1)
    cx = (x0 + x1) // 2
    sx = cx - total // 2
    asc, desc = font.getmetrics()
    cy = y0 + (bh - (asc + desc)) // 2
    x = sx
    for c, cw in zip(text, widths):
        draw.text((x, cy), c, font=font, fill=kit.eyebrow_fill)
        x += cw + track


# --- illustration placement --------------------------------------------------
def place_illustration(page: Image.Image, illo: Image.Image, box: Box,
                       blend: str, paper: tuple[int, int, int],
                       valign: str = "bottom",
                       tight_crop: bool = False) -> None:
    """Composite the illustration into ``box`` (centered horizontally).

    ``tight_crop`` (CRM-generated templates only) crops through the vignette's
    faint halo so the visible art fills the zone instead of floating small;
    the shipped Father's Day template keeps the original loose crop.
    """
    w, h = page.size
    x0, y0, x1, y1 = box.px(w, h)
    zw, zh = x1 - x0, y1 - y0
    if blend == "green":
        art = chroma_key_green(illo)
        bbox = art.getchannel("A").getbbox()
        if bbox:
            art = art.crop(bbox)
    else:
        art = _autocrop_content(illo,
                                bounds_thresh=60 if tight_crop else None)
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
        ay = y1 - art.height
    page.alpha_composite(art, (ax, ay))


# --- layers + page ----------------------------------------------------------
def illustration_layer(template: Image.Image, illo: Image.Image | None,
                       blend: str = "cream", box: Box | None = None,
                       valign: str = "bottom",
                       tight_crop: bool = False) -> Image.Image:
    layer = Image.new("RGBA", template.size, (0, 0, 0, 0))
    if illo is not None:
        place_illustration(layer, illo, box or ART_BOX, blend,
                           paper_color(template), valign,
                           tight_crop=tight_crop)
    return layer


def text_layer(template: Image.Image, eyebrow: str, line: str,
               box: Box | None = None,
               kit: style.StyleKit | None = None) -> Image.Image:
    layer = Image.new("RGBA", template.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw_eyebrow(draw, eyebrow, EYEBROW_BOX, template.size, kit=kit)
    draw_main_line(draw, line, box or TEXT_BOX, template.size, kit=kit)
    return layer


def compose_page(*, eyebrow: str, line: str, illo: Image.Image | None,
                 blend: str = "cream", template: Image.Image | None = None,
                 layout=None,
                 kit: style.StyleKit | None = None) -> Image.Image:
    base = (template or load_template(kit))
    lay = layout or LAYOUTS["text_top"]
    generated = (kit or style.DEFAULT_KIT).generated_template
    if generated:
        lay = style.safe_layout(lay)
    page = base.convert("RGBA")
    page = Image.alpha_composite(
        page, illustration_layer(base, illo, blend, lay.art_box, lay.art_valign,
                                 tight_crop=generated))
    page = Image.alpha_composite(
        page, text_layer(base, eyebrow, line, lay.text_box, kit=kit))
    return page.convert("RGB")
