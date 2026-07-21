"""Unit tests for the admin recipe validator (no DB / no app)."""
from flashback.http.routes.admin_tribute_config import validate_recipe


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
