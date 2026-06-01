"""Unit tests for the shared artifact preset registry."""

import pytest

from flashback.artifacts.presets import (
    DEFAULT_PRESET_SLUG,
    apply_preset,
    list_presets,
    resolve_preset,
)


class TestListPresets:
    def test_default_is_first(self):
        presets = list_presets()
        assert presets[0]["slug"] == DEFAULT_PRESET_SLUG
        assert presets[0]["is_default"] is True

    def test_only_one_default(self):
        presets = list_presets()
        defaults = [p for p in presets if p["is_default"]]
        assert len(defaults) == 1

    def test_required_fields_present(self):
        for p in list_presets():
            assert p["slug"]
            assert p["label"]
            assert p["description"]
            assert "is_default" in p

    def test_slugs_are_unique(self):
        slugs = [p["slug"] for p in list_presets()]
        assert len(slugs) == len(set(slugs))


class TestResolvePreset:
    def test_none_resolves_to_default(self):
        assert resolve_preset(None) == DEFAULT_PRESET_SLUG

    def test_known_slug_returns_self(self):
        assert resolve_preset("golden_hour") == "golden_hour"

    def test_unknown_slug_raises(self):
        with pytest.raises(ValueError):
            resolve_preset("not_a_real_preset")


class TestApplyPreset:
    def test_default_is_no_op(self):
        prompt = "A wood-paneled kitchen at dawn"
        assert apply_preset(prompt, None) == prompt
        assert apply_preset(prompt, DEFAULT_PRESET_SLUG) == prompt

    def test_golden_hour_appends_modifier(self):
        prompt = "A wood-paneled kitchen at dawn"
        out = apply_preset(prompt, "golden_hour")
        assert prompt in out
        assert "golden-hour" in out.lower() or "golden hour" in out.lower()
        assert len(out) > len(prompt)

    def test_twilight_appends_modifier(self):
        out = apply_preset("A street corner", "twilight")
        assert "twilight" in out.lower() or "blue-hour" in out.lower()

    def test_unknown_preset_raises(self):
        with pytest.raises(ValueError):
            apply_preset("anything", "bogus_preset")

    def test_empty_prompt_with_default_returns_empty(self):
        assert apply_preset("", None) == ""

    def test_empty_prompt_with_non_default_returns_modifier_only(self):
        out = apply_preset("", "golden_hour")
        assert out  # non-empty
        assert "golden" in out.lower()
