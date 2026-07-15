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
