"""Compositing primitives — autocrop bounds against vignette halos + flecks.

The strict (tight) crop is opt-in via ``bounds_thresh``: only CRM-generated
visual themes use it. The default path must stay byte-identical for the
shipped Father's Day pipeline.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from flashback.page_render.primitives import autocrop_content

PAPER = (245, 240, 230)


def _vignette_image(w: int = 400, h: int = 400) -> Image.Image:
    """Paper page with a solid painted core, a wide faint wash halo around
    it, and one stray dark fleck near the corner — the shape of a Gemini
    cream-blend illustration."""
    arr = np.tile(np.array(PAPER, np.uint8), (h, w, 1))
    arr[100:300, 60:340] = (235, 228, 215)  # faint halo: diff ~27, dense
    arr[150:250, 100:300] = (90, 70, 50)  # solid core
    arr[8:10, 8:10] = (40, 40, 40)  # stray fleck
    return Image.fromarray(arr, "RGB")


def test_default_crop_keeps_the_old_loose_bounds() -> None:
    """Father's Day regression guard: without bounds_thresh the box still
    stretches to any above-threshold pixel (the fleck near the corner)."""
    out = autocrop_content(_vignette_image())
    assert out.height > 300  # fleck at y=8 through halo at y=300 (+pad)


def test_tight_crop_ignores_faint_halo_and_stray_fleck() -> None:
    img = _vignette_image()
    out = autocrop_content(img, bounds_thresh=60)
    # crop ~= the solid core (+pad), not the halo box and not the fleck
    pad = int(400 * 0.015) + 1
    assert out.width <= (300 - 100) + 2 * pad
    assert out.height <= (250 - 150) + 2 * pad
    assert out.width >= (300 - 100) - 2
    # the visible art fills most of the crop now
    arr = np.asarray(out).astype(np.int16)
    diff = np.abs(arr - np.array(PAPER, np.int16)).sum(axis=2)
    assert (diff > 60).mean() > 0.5


def test_autocrop_blank_page_returns_input() -> None:
    img = Image.new("RGB", (100, 100), PAPER)
    assert autocrop_content(img).size == (100, 100)
    assert autocrop_content(img, bounds_thresh=60).size == (100, 100)


def test_tight_crop_faint_only_content_falls_back_to_loose_bounds() -> None:
    """Content that never crosses the strong threshold must still crop via
    the original any-pixel path instead of returning an empty box."""
    arr = np.tile(np.array(PAPER, np.uint8), (200, 200, 1))
    arr[80:120, 80:120] = (235, 228, 215)  # faint wash only (diff ~27)
    out = autocrop_content(Image.fromarray(arr, "RGB"), bounds_thresh=60)
    assert 0 < out.width < 200 and 0 < out.height < 200
