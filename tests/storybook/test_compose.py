"""Compositor — green-zone detection, box math, fills (synthetic templates)."""

from __future__ import annotations

import numpy as np
from PIL import Image

from flashback.storybook.compose import (
    expand_box,
    fit_fill,
    gemini_aspect,
    green_components,
    grid_boxes,
    grid_page_base,
    make_cover,
    panel_boxes,
)


def _synthetic_template(tmp_path, name="t.png"):
    arr = np.full((400, 300, 3), 245, dtype=np.uint8)
    arr[40:160, 30:270] = (0, 255, 0)  # panel 1
    arr[200:360, 30:270] = (0, 255, 0)  # panel 2
    p = tmp_path / name
    Image.fromarray(arr).save(p)
    return str(p)


def test_green_components_finds_both_zones(tmp_path) -> None:
    boxes, (h, w) = green_components(_synthetic_template(tmp_path))
    assert (h, w) == (400, 300)
    assert len(boxes) == 2


def test_panel_boxes_reading_order(tmp_path) -> None:
    boxes = panel_boxes(_synthetic_template(tmp_path))
    assert len(boxes) == 2
    assert boxes[0][1] < boxes[1][1]  # top panel first


def test_grid_boxes_synthesizes_n_stacked(tmp_path) -> None:
    boxes = grid_boxes(_synthetic_template(tmp_path), 3)
    assert len(boxes) == 3
    assert boxes[0][1] < boxes[1][1] < boxes[2][1]
    # all synthesized panels share the union's horizontal extent
    assert len({(b[0], b[2]) for b in boxes}) == 1


def test_grid_page_base_wipes_green_to_paper(tmp_path) -> None:
    path = _synthetic_template(tmp_path)
    base = grid_page_base(Image.open(path).convert("RGB"), path)
    arr = np.asarray(base)
    assert arr[100, 150, 1] > 200  # was pure green, now paper
    assert abs(int(arr[100, 150, 0]) - int(arr[100, 150, 1])) < 30


def test_fit_fill_exact_size() -> None:
    art = Image.new("RGB", (100, 50))
    assert fit_fill(art, 240, 120).size == (240, 120)


def test_gemini_aspect_buckets() -> None:
    assert gemini_aspect((0, 0, 160, 90)) == "16:9"
    assert gemini_aspect((0, 0, 90, 160)) == "9:16"
    assert gemini_aspect((0, 0, 100, 100)) == "1:1"


def test_expand_box_stays_in_page() -> None:
    box = expand_box((50, 100, 250, 260), 300, 400)
    x0, y0, x1, y1 = box
    assert 0 <= x0 < x1 <= 300
    assert 0 <= y0 < y1 <= 400


def test_make_cover_renders_title_and_art(tmp_path) -> None:
    cover_path = _synthetic_template(tmp_path, "cover.png")
    art = Image.new("RGB", (300, 400), (120, 90, 60))
    out = make_cover(cover_path, "A Title", "Subject", art=art)
    assert out.size == (300, 400)
