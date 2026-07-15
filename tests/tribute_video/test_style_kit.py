"""StyleKit: config-driven template/fonts/inks/audio with builtin fallback."""

from __future__ import annotations

from PIL import Image

from flashback.tribute_video import compose, style
from flashback.tribute_video.context import RenderContext, build_context_dict
from flashback.tribute_video.style import (
    DEFAULT_KIT,
    kit_from_style_dict,
)


def test_none_style_is_default_kit() -> None:
    assert kit_from_style_dict(None) == DEFAULT_KIT


def test_hex_parsing_and_registry_resolution() -> None:
    kit = kit_from_style_dict(
        {
            "fonts": {"main_slug": "eb_garamond", "eyebrow_slug": "playfair_italic"},
            "ink": {"main_fill": "#112233", "eyebrow_fill": "#abcdef"},
            "audio_slug": "sentimental_piano",
        }
    )
    assert kit.main_font == style.FONT_REGISTRY["eb_garamond"]
    assert kit.eyebrow_font == style.FONT_REGISTRY["playfair_italic"]
    assert kit.main_fill == (0x11, 0x22, 0x33)
    assert kit.eyebrow_fill == (0xAB, 0xCD, 0xEF)
    assert kit.audio_path == style.AUDIO_REGISTRY["sentimental_piano"]


def test_unknown_slugs_and_bad_hex_fall_back() -> None:
    kit = kit_from_style_dict(
        {
            "fonts": {"main_slug": "comic_sans", "eyebrow_slug": None},
            "ink": {"main_fill": "not-a-color"},
            "audio_slug": "dubstep",
        }
    )
    assert kit.main_font == DEFAULT_KIT.main_font
    assert kit.eyebrow_font == DEFAULT_KIT.eyebrow_font
    assert kit.main_fill == DEFAULT_KIT.main_fill
    assert kit.audio_path == DEFAULT_KIT.audio_path


def test_template_override_path_wins(tmp_path) -> None:
    override = tmp_path / "custom-template.jpg"
    Image.new("RGB", (10, 16), (240, 230, 210)).save(override, "JPEG")
    kit = kit_from_style_dict({"audio_slug": "sentimental_piano"},
                              template_override_path=str(override))
    assert kit.template_path == str(override)
    img = compose.load_template(kit)
    assert img.size == (10, 16)


def test_render_context_roundtrips_new_fields() -> None:
    d = build_context_dict(
        subject_name="Arjun", relationship="best friend", gt_context="",
        candidates=[{"id": "m1"}], video_put_url="v", pdf_put_url="p",
        style={"visual_theme_id": "vt1", "fonts": {}, "ink": {},
               "audio_slug": "sentimental_piano"},
        profile_id="pf1", campaign_id="c1",
        voice_block="VB", opener_style="OS", art_mood="AM",
        fallback_opener="FO {name}", fallback_closing="FC {name}",
        composed_at="t",
    )
    ctx = RenderContext.from_dict(d, tribute_id="t1", person_id="p1")
    assert ctx.style["visual_theme_id"] == "vt1"
    assert (ctx.profile_id, ctx.campaign_id) == ("pf1", "c1")
    assert (ctx.voice_block, ctx.opener_style, ctx.art_mood) == ("VB", "OS", "AM")
    assert ctx.fallback_opener == "FO {name}"


def test_legacy_context_dict_defaults_new_fields() -> None:
    legacy = {
        "subject_name": "C", "relationship": None, "gt_context": "",
        "candidates": [], "video_put_url": "v", "pdf_put_url": "p",
        "composed_at": "t",
    }
    ctx = RenderContext.from_dict(legacy, tribute_id="t1", person_id="p1")
    assert ctx.style is None
    assert ctx.voice_block == "" and ctx.fallback_opener == ""
    # None style -> the shipped kit, byte-identical behavior
    from flashback.workers.tribute_render.worker import build_style_kit

    kit = build_style_kit(ctx, pool=None, tmpdir=".")
    assert kit == DEFAULT_KIT


def test_every_registry_font_loads_and_composes(tmp_path) -> None:
    """Each curated font must be loadable by Pillow and render a line —
    a corrupt/missing font file should fail here, not mid-render."""
    from PIL import ImageFont

    override = tmp_path / "t.jpg"
    Image.new("RGB", (200, 320), (250, 245, 235)).save(override, "JPEG")
    for slug, path in style.FONT_REGISTRY.items():
        ImageFont.truetype(path, 24)  # loads without error
        kit = kit_from_style_dict(
            {"fonts": {"main_slug": slug, "eyebrow_slug": slug}},
            template_override_path=str(override),
        )
        page = compose.compose_page(
            eyebrow="", line=f"A line set in {slug}.", illo=None, kit=kit
        )
        assert page.size == (200, 320)


def test_compose_page_with_custom_kit(tmp_path) -> None:
    override = tmp_path / "t.jpg"
    Image.new("RGB", (100, 160), (250, 245, 235)).save(override, "JPEG")
    kit = kit_from_style_dict(
        {"ink": {"main_fill": "#204060"}},
        template_override_path=str(override),
    )
    page = compose.compose_page(eyebrow="", line="A short bright line.",
                                illo=None, kit=kit)
    assert page.size == (100, 160)


def test_generated_template_flag() -> None:
    assert kit_from_style_dict({"audio_slug": "x"}).generated_template is False
    kit = kit_from_style_dict({}, template_override_path="/tmp/t.jpg")
    assert kit.generated_template is True


def test_safe_layout_clamps_every_layout_inside_bounds() -> None:
    for lay in style.LAYOUTS.values():
        safe = style.safe_layout(lay)
        tb, ab = safe.text_box, safe.art_box
        assert tb.x0 >= style.SAFE_TEXT_BOUNDS.x0
        assert tb.x1 <= style.SAFE_TEXT_BOUNDS.x1
        assert tb.y1 <= style.SAFE_TEXT_BOUNDS.y1
        assert ab.x0 >= style.SAFE_ART_BOUNDS.x0
        assert ab.x1 <= style.SAFE_ART_BOUNDS.x1
        assert ab.y1 <= style.SAFE_ART_BOUNDS.y1
        assert tb.x0 < tb.x1 and tb.y0 < tb.y1  # still a real box
        assert ab.x0 < ab.x1 and ab.y0 < ab.y1
        assert safe.art_valign == lay.art_valign


def test_generated_template_keeps_ink_off_the_border_band(tmp_path) -> None:
    """On a generated border template the text must stay inside the safe
    interior — the 2026-07-15 sample page had lines overlapping the frame
    art because TEXT_BOX ran flush against the border budget."""
    import numpy as np

    w, h = 450, 800
    override = tmp_path / "t.jpg"
    Image.new("RGB", (w, h), (250, 245, 235)).save(override, "JPEG")
    kit = kit_from_style_dict(
        {"ink": {"main_fill": "#000000"}},
        template_override_path=str(override),
    )
    line = "Every family has a beginning, and ours is called Chandraiah."
    page = compose.compose_page(eyebrow="", line=line, illo=None, kit=kit)
    arr = np.asarray(page.convert("L"))
    ink_cols = np.where((arr < 128).any(axis=0))[0]
    assert ink_cols.size > 0  # something rendered
    assert ink_cols.min() >= int(style.SAFE_TEXT_BOUNDS.x0 * w)
    assert ink_cols.max() <= int(style.SAFE_TEXT_BOUNDS.x1 * w)
    # the builtin (thin-frame) template keeps the original wider box
    default_page = compose.compose_page(eyebrow="", line=line, illo=None,
                                        kit=DEFAULT_KIT)
    assert default_page.size == Image.open(DEFAULT_KIT.template_path).size
