"""Unit tests for the admin recipe validator + boundary translation
(no DB / no app)."""
from flashback.http.routes.admin_tribute_config import (
    _flatten_recipe,
    _nest_recipe_row,
    validate_recipe,
)


def test_empty_recipe_is_valid():
    assert validate_recipe({}) == []
    assert validate_recipe({"layout_palette": [], "motion_preset": ""}) == []


def test_valid_recipe_passes():
    errs = validate_recipe({
        "layout_palette": ["scrapbook", "type_over_crop", "fullbleed_caption"],
        "layout_pins": {"opener": "split_duotone", "closing": "fullbleed_caption"},
        "pacing": {"hold": 2.4, "transition": 0.7},
        "motion_preset": "punchy",
    })
    assert errs == []


def test_unknown_palette_slug_rejected():
    errs = validate_recipe({"layout_palette": ["scrapbook", "bogus_layout"]})
    assert any("layout_palette" in e and "bogus_layout" in e for e in errs)


def test_bad_pin_role_and_slug_rejected():
    errs = validate_recipe({"layout_pins": {"middle": "scrapbook",
                                            "opener": "nope"}})
    assert any("unknown role 'middle'" in e for e in errs)
    assert any("layout_pins.opener" in e for e in errs)


def test_unknown_motion_preset_rejected():
    errs = validate_recipe({"motion_preset": "explode"})
    assert any("motion_preset" in e for e in errs)


def test_non_numeric_pacing_rejected():
    errs = validate_recipe({"pacing": {"hold": "fast"}})
    assert any("pacing.hold" in e for e in errs)


def test_render_engine_values():
    assert validate_recipe({"render_engine": ""}) == []
    assert validate_recipe({"render_engine": "legacy"}) == []
    assert validate_recipe({"render_engine": "remotion"}) == []
    errs = validate_recipe({"render_engine": "pillow"})
    assert any("render_engine" in e for e in errs)


def test_flatten_recipe_unpacks_nested_block():
    flat = _flatten_recipe("tribute_visual_themes", {
        "slug": "fd",
        "recipe": {"layout_palette": ["framed_hero"], "render_engine": "legacy"},
    })
    assert flat["layout_palette"] == ["framed_hero"]
    assert flat["render_engine"] == "legacy"
    assert "recipe" not in flat
    # keys the recipe omits are written as EMPTY (clear beats stale carry)
    assert flat["layout_pins"] == {} and flat["pacing"] == {}
    assert flat["motion_preset"] == ""


def test_flatten_recipe_leaves_flat_and_other_tables_alone():
    flat_payload = {"slug": "fd", "layout_palette": ["scrapbook"]}
    assert _flatten_recipe("tribute_visual_themes", flat_payload) == flat_payload
    campaign = {"slug": "fd", "recipe": {"render_engine": "legacy"}}
    assert _flatten_recipe("tribute_campaigns", campaign) == campaign
    # non-dict recipe stays in place for _validate to flag
    bad = {"slug": "fd", "recipe": "legacy"}
    assert _flatten_recipe("tribute_visual_themes", bad) == bad


def test_nest_recipe_row_round_trip():
    row = {"id": "x", "slug": "fd", "layout_palette": ["framed_hero"],
           "layout_pins": {}, "pacing": {"hold": 3.0}, "motion_preset": "calm",
           "render_engine": "legacy"}
    nested = _nest_recipe_row("tribute_visual_themes", row)
    assert nested["recipe"] == {"layout_palette": ["framed_hero"],
                                "layout_pins": {}, "pacing": {"hold": 3.0},
                                "motion_preset": "calm",
                                "render_engine": "legacy"}
    assert "layout_palette" not in nested
    # and flattening the nested shape restores the columns
    flat = _flatten_recipe("tribute_visual_themes", nested)
    assert flat["render_engine"] == "legacy" and flat["pacing"] == {"hold": 3.0}
