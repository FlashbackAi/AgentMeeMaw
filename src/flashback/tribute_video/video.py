"""Render the storybook pages into an MP4.

Page 1 reveals in layers (paper -> illustration -> line). Each following page
ARRIVES through a transition that carries its illustration in, then the line
fades on top, then a slow Ken Burns drift. Transitions: "bleed" (watercolour ink
wash, default), "turn" (page-turn slide), "dip" (crossfade). Encoded with the
bundled imageio-ffmpeg (no system ffmpeg needed).
"""
from __future__ import annotations

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageFilter

OUT_W, OUT_H = 896, 1600          # multiples of 16 for libx264
ZOOM_MAX = 1.05

T_INTRO = 0.6
T_ILLO = 1.0
T_TEXT = 0.8
T_HOLD = 2.4
T_TRANS = 1.1
T_OUT_HOLD = 1.2
T_OUT_FADE = 1.0


def _faded(layer: Image.Image, f: float) -> Image.Image:
    if f >= 1.0:
        return layer
    if f <= 0.0:
        return Image.new("RGBA", layer.size, (0, 0, 0, 0))
    alpha = layer.getchannel("A").point(lambda v: int(v * f))
    out = layer.copy()
    out.putalpha(alpha)
    return out


def _zoom(img: Image.Image, s: float) -> Image.Image:
    if abs(s - 1.0) < 1e-3:
        return img
    w, h = img.size
    cw, ch = int(w / s), int(h / s)
    x, y = (w - cw) // 2, (h - ch) // 2
    return img.crop((x, y, x + cw, y + ch)).resize((w, h), Image.BILINEAR)


def _np(img: Image.Image) -> np.ndarray:
    if img.size != (OUT_W, OUT_H):
        img = img.resize((OUT_W, OUT_H), Image.BILINEAR)
    return np.asarray(img.convert("RGB"))


def _bleed_field(size: tuple[int, int], seed: int) -> np.ndarray:
    """Smooth organic noise in [0,1] (h,w) used as the ink-bleed reveal order."""
    rng = np.random.default_rng(seed)
    sw, sh = max(8, size[0] // 12), max(8, size[1] // 12)
    small = (rng.random((sh, sw)) * 255).astype(np.uint8)
    img = (Image.fromarray(small).resize(size, Image.BICUBIC)
           .filter(ImageFilter.GaussianBlur(size[0] * 0.02)))
    a = np.asarray(img).astype(np.float32)
    return (a - a.min()) / (a.max() - a.min() + 1e-6)


def _transition_frames(a: Image.Image, b: Image.Image, kind: str, n: int,
                       seed: int):
    w, h = a.size
    if kind == "turn":
        for k in range(n):
            t = k / max(1, n - 1)
            e = t * t * (3 - 2 * t)
            fr = b.copy()
            fr.paste(a, (int(-w * e), 0))
            yield fr
    elif kind == "dip":
        for k in range(n):
            yield Image.blend(a, b, k / max(1, n - 1))
    else:  # bleed
        field = _bleed_field((w, h), seed)
        edge = 0.16
        af = np.asarray(a.convert("RGB")).astype(np.float32)
        bf = np.asarray(b.convert("RGB")).astype(np.float32)
        for k in range(n):
            t = k / max(1, n - 1)
            thr = t * (1 + 2 * edge) - edge
            alpha = np.clip((thr - field) / edge, 0.0, 1.0)[..., None]
            fr = (bf * alpha + af * (1 - alpha)).astype(np.uint8)
            yield Image.fromarray(fr, "RGB")


class Page:
    """The three layers of one page, pre-flattened for animation."""

    def __init__(self, paper: Image.Image, illo_layer: Image.Image,
                 text_layer: Image.Image):
        self.paper = paper.convert("RGB")
        self.illo = illo_layer
        self.text = text_layer
        self.base = Image.alpha_composite(
            paper.convert("RGBA"), illo_layer).convert("RGB")

    def final(self) -> Image.Image:
        f = _zoom(self.base, ZOOM_MAX).convert("RGBA")
        return Image.alpha_composite(f, self.text).convert("RGB")


def render_video(pages: list[Page], out_path: str, fps: int = 30,
                 transition: str = "bleed") -> None:
    paper = pages[0].paper

    def nfr(sec: float) -> int:
        return max(1, int(round(sec * fps)))

    writer = imageio.get_writer(
        out_path, fps=fps, codec="libx264", quality=None,
        macro_block_size=1, pixelformat="yuv420p",
        output_params=["-crf", "20", "-preset", "medium"],
    )

    def emit(img: Image.Image) -> None:
        writer.append_data(_np(img))

    def reveal_text_hold(pg: Page) -> None:
        nt, nh = nfr(T_TEXT), nfr(T_HOLD)
        span, gi = nt + nh, 0
        for k in range(nt):
            z = 1.0 + (ZOOM_MAX - 1.0) * (gi / max(1, span - 1)); gi += 1
            base = _zoom(pg.base, z).convert("RGBA")
            emit(Image.alpha_composite(base, _faded(pg.text, k / max(1, nt - 1)))
                 .convert("RGB"))
        for k in range(nh):
            z = 1.0 + (ZOOM_MAX - 1.0) * (gi / max(1, span - 1)); gi += 1
            base = _zoom(pg.base, z).convert("RGBA")
            emit(Image.alpha_composite(base, pg.text).convert("RGB"))

    try:
        white = Image.new("RGB", paper.size, (255, 255, 255))
        ni = nfr(T_INTRO)
        for k in range(ni):
            emit(Image.blend(white, paper, k / max(1, ni - 1)))

        p0 = pages[0]
        na = nfr(T_ILLO)
        for k in range(na):
            emit(Image.alpha_composite(
                p0.paper.convert("RGBA"), _faded(p0.illo, k / max(1, na - 1)))
                .convert("RGB"))
        reveal_text_hold(p0)

        for idx in range(1, len(pages)):
            prev, cur = pages[idx - 1], pages[idx]
            for fr in _transition_frames(prev.final(), cur.base, transition,
                                         nfr(T_TRANS), seed=1000 + idx):
                emit(fr)
            reveal_text_hold(cur)

        last = pages[-1].final()
        for _ in range(nfr(T_OUT_HOLD)):
            emit(last)
        nf = nfr(T_OUT_FADE)
        for k in range(nf):
            emit(Image.blend(last, paper, k / max(1, nf - 1)))
    finally:
        writer.close()
