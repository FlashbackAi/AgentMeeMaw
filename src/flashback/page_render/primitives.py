"""Shared Pillow compositing primitives (extracted from tribute_video.compose).

Byte-identical behavior to the tribute originals; both the tribute compositor
and the storybook compositor build on these.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageFont


# --- paper / colour ----------------------------------------------------------
def paper_color(page: Image.Image) -> tuple[int, int, int]:
    """Median colour of a blank patch in the upper-middle of the page."""
    w, h = page.size
    patch = page.convert("RGB").crop(
        (int(0.40 * w), int(0.30 * h), int(0.60 * w), int(0.36 * h)))
    arr = np.asarray(patch).reshape(-1, 3)
    return tuple(int(v) for v in np.median(arr, axis=0))


# --- fonts --------------------------------------------------------------------
def load_font(path: str, size: int, fallback: str, weight: int | None = None):
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


def text_width(font, s: str) -> int:
    return int(font.getbbox(s)[2] - font.getbbox(s)[0])


def wrap_words(words: list[str], font, max_w: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if text_width(font, trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# --- illustration blends -------------------------------------------------------
def chroma_key_green(img: Image.Image) -> Image.Image:
    """Key out chroma-green -> RGBA with the subject isolated (despilled)."""
    arr = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    greenness = g - np.maximum(r, b)
    alpha = np.clip((45 - greenness) / 35.0, 0.0, 1.0)
    alpha = (alpha * 255).astype(np.uint8)
    g2 = g.copy()
    spill = greenness > 0
    g2[spill] = np.maximum(r, b)[spill]
    rgba = np.dstack([r, g2, b, alpha]).astype(np.uint8)
    out = Image.fromarray(rgba, "RGBA")
    a = out.getchannel("A").filter(ImageFilter.GaussianBlur(1.2))
    out.putalpha(a)
    return out


def tone_match(art: Image.Image, paper: tuple[int, int, int]) -> Image.Image:
    """Shift the art so its (bright) paper background equals the template's."""
    arr = np.asarray(art.convert("RGB")).astype(np.float32)
    lum = arr.mean(axis=2)
    thr = np.percentile(lum, 80)
    bg = arr[lum >= thr].reshape(-1, 3).mean(axis=0)
    delta = np.asarray(paper, dtype=np.float32) - bg
    out = np.clip(arr + delta, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB")


def feather_mask(size: tuple[int, int], frac: float = 0.06) -> Image.Image:
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


def autocrop_content(img: Image.Image, thresh: int = 24,
                     pad_frac: float = 0.015) -> Image.Image:
    """Trim the blank paper margin around the painted content."""
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
