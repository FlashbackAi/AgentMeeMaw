"""The contributor-message page never overflows (prod 2026-07-19: a long
message spilled over the top border and through the portrait)."""

from __future__ import annotations

import numpy as np
from PIL import Image

from flashback.tribute.message_llm import MESSAGE_MAX_CHARS, clamp_message
from flashback.tribute_video import compose, style
from flashback.tribute_video.style import kit_from_style_dict

SHORT = "I love you more than words can say."
LONG = (
    "You've always kept talking about the day we met and how you told me "
    "everything would be fine, how much we have laughed and how you respect "
    "my family and how loyal you have been through every single thing that "
    "life has thrown at the both of us over all these years together. "
) * 3  # ~120 words
ABSURD = "friendship memories laughter loyalty chai " * 120  # ~600 words


def _template(tmp_path, w=899, h=1600):
    # Production geometry: the fitter works in template PIXELS, so fit
    # outcomes only hold at the real 899x1600 page size.
    p = tmp_path / "t.jpg"
    Image.new("RGB", (w, h), (250, 245, 235)).save(p, "JPEG")
    return Image.open(p).convert("RGB"), str(p)


def test_short_message_keeps_layout_and_art(tmp_path) -> None:
    template, path = _template(tmp_path)
    kit = kit_from_style_dict({}, template_override_path=path)
    base = style.safe_layout(style.layout_for("message", 0))
    lay, include_art, text = compose.plan_message_page(
        template, SHORT, base, kit=kit)
    assert include_art is True
    assert text == SHORT
    assert lay is base


def test_long_message_drops_art_and_expands(tmp_path) -> None:
    template, path = _template(tmp_path)
    kit = kit_from_style_dict({}, template_override_path=path)
    base = style.safe_layout(style.layout_for("message", 0))
    lay, include_art, text = compose.plan_message_page(
        template, LONG, base, kit=kit)
    assert include_art is False
    assert text == LONG  # fits the expanded box without truncation
    assert lay.text_box.y1 > base.text_box.y1  # took the art zone


def test_absurd_message_truncates_instead_of_spilling(tmp_path) -> None:
    template, path = _template(tmp_path)
    kit = kit_from_style_dict({}, template_override_path=path)
    base = style.safe_layout(style.layout_for("message", 0))
    lay, include_art, text = compose.plan_message_page(
        template, ABSURD, base, kit=kit)
    assert include_art is False
    assert text.endswith("…")
    assert len(text) < len(ABSURD)


def test_long_message_ink_stays_inside_the_page(tmp_path) -> None:
    """Compose the planned page and check no ink lands in the border band
    or above the text box — the exact prod failure shape."""
    template, path = _template(tmp_path)
    kit = kit_from_style_dict(
        {"ink": {"main_fill": "#000000"}}, template_override_path=path)
    base = style.safe_layout(style.layout_for("message", 0))
    lay, include_art, text = compose.plan_message_page(
        template, LONG, base, kit=kit)
    page = compose.compose_page(eyebrow="", line=text, illo=None,
                                layout=lay, kit=kit)
    w, h = page.size
    arr = np.asarray(page.convert("L"))
    ink_rows = np.where((arr < 128).any(axis=1))[0]
    assert ink_rows.size > 0
    assert ink_rows.min() >= int(lay.text_box.y0 * h) - 1
    assert ink_rows.max() <= int(0.90 * h)  # inside the safe interior


def test_clamp_message() -> None:
    assert clamp_message(SHORT) == SHORT
    clamped = clamp_message(ABSURD)
    assert len(clamped) <= MESSAGE_MAX_CHARS + 2
    assert clamped.endswith("…")
    assert not clamped[:-2].endswith(" ")  # word boundary, tidy tail
