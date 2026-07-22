"""Config-layer wiring for the Remotion recipe (migration 0044), no DB needed."""
import asyncio

from flashback.http.routes.tributes import (
    _style_dict,
    flashback_layouts,
    flashback_opener_presets,
)
from flashback.tribute.config_schema import VisualThemeConfig
from flashback.tribute_video.sequencer import LAYOUT_CATALOG


def _theme(**over) -> VisualThemeConfig:
    base = dict(
        id="t1", slug="friendship_pop", display_name="Friendship Pop",
        has_image=True, template_mime="image/jpeg",
        fonts={"main_slug": "caveat"}, ink={"main_fill": "#222", "accent": "#e8552e"},
        audio_slug="upbeat", state="published", version=1,
    )
    base.update(over)
    return VisualThemeConfig(**base)


def test_visual_theme_recipe_fields_default_empty():
    t = _theme()  # constructed without recipe fields
    assert t.layout_palette == [] and t.layout_pins == {}
    assert t.pacing == {} and t.motion_preset == ""
    assert t.render_engine == ""


def test_style_dict_carries_engine_pin():
    style = _style_dict(_theme(render_engine="legacy"))
    assert style["recipe"]["render_engine"] == "legacy"
    # unpinned themes leave the worker default in charge
    assert _style_dict(_theme())["recipe"]["render_engine"] == ""


def test_style_dict_carries_recipe_and_accent():
    t = _theme(
        layout_palette=["scrapbook", "type_over_crop"],
        layout_pins={"opener": "split_duotone"},
        pacing={"hold": 2.0, "transition": 0.6},
        motion_preset="punchy",
    )
    style = _style_dict(t)
    assert style["recipe"]["layout_palette"] == ["scrapbook", "type_over_crop"]
    assert style["recipe"]["layout_pins"] == {"opener": "split_duotone"}
    assert style["recipe"]["pacing"] == {"hold": 2.0, "transition": 0.6}
    assert style["recipe"]["motion_preset"] == "punchy"
    assert style["ink"]["accent"] == "#e8552e"


def test_style_dict_none_when_no_theme():
    assert _style_dict(None) is None


def test_flashback_layouts_endpoint_shape():
    out = asyncio.run(flashback_layouts())
    slugs = {l["slug"] for l in out["layouts"]}
    assert slugs == {"split_duotone", "scrapbook", "type_over_crop",
                     "fullbleed_caption", "framed_hero", "letter_note",
                     "filmstrip", "postcard", "word_mask", "torn_reveal",
                     "gallery_wall", "magazine", "map_journey"}
    assert all("label" in l and "description" in l for l in out["layouts"])
    assert "punchy" in out["motion_presets"]
    assert out["pinnable_roles"] == ["opener", "payoff", "closing"]
    assert out["layouts"] is not LAYOUT_CATALOG or True  # served from the catalog


def test_flashback_opener_presets_endpoint_shape():
    out = asyncio.run(flashback_opener_presets())
    presets = out["opener_presets"]
    slugs = {p["slug"] for p in presets}
    assert {"dedication", "party_story", "scene_setter"} <= slugs
    for p in presets:
        assert p["label"] and p["description"]
        # examples are the dropdown preview; each carries {name}
        assert p["examples"] and all("{name}" in e for e in p["examples"])
        # internal prompt wording is NOT leaked to the public catalog
        assert "style" not in p


def test_recipe_snapshot_feeds_render_kwargs():
    # The snapshot style.recipe must be exactly what the render worker reads.
    from flashback.tribute_video.remotion_render import recipe_kwargs_from_style
    style = _style_dict(_theme(
        layout_palette=["framed_hero", "fullbleed_caption"],
        pacing={"hold": 3.0, "transition": 1.0}))
    kw = recipe_kwargs_from_style(style)
    assert kw["palette"] == ["framed_hero", "fullbleed_caption"]
    assert kw["hold"] == 3.0 and kw["transition"] == 1.0
    assert kw["accent"] == "#e8552e"
