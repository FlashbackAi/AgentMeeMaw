"""Unit tests for scene-art prompt composition."""

import pytest

from flashback.artifacts.compose import SCENE_NEGATIVE_PROMPT, compose_scene_prompt


class TestComposeSceneBasics:
    def test_base_only_returns_base(self):
        base = "A wood-paneled kitchen at dawn"
        assert compose_scene_prompt(base_prompt=base) == base

    def test_empty_base_no_inputs_returns_empty(self):
        assert compose_scene_prompt(base_prompt="") == ""

    def test_default_preset_does_not_modify(self):
        base = "A red truck in snow"
        assert compose_scene_prompt(base_prompt=base, preset=None) == base


class TestComposeSceneStacking:
    def test_prior_and_new_stack_in_order(self):
        out = compose_scene_prompt(
            base_prompt="An old porch at dusk",
            prior_instructions=["add a swing"],
            instructions="and a worn quilt",
        )
        base_idx = out.index("An old porch at dusk")
        swing_idx = out.index("add a swing")
        quilt_idx = out.index("and a worn quilt")
        assert base_idx < swing_idx < quilt_idx

    def test_multiple_prior_instructions_chained(self):
        out = compose_scene_prompt(
            base_prompt="A field",
            prior_instructions=["with tall grass", "and wildflowers"],
            instructions="under low cloud",
        )
        i_grass = out.index("tall grass")
        i_flowers = out.index("wildflowers")
        i_cloud = out.index("low cloud")
        assert i_grass < i_flowers < i_cloud

    def test_blank_entries_skipped(self):
        out = compose_scene_prompt(
            base_prompt="A barn",
            prior_instructions=["", "  ", "with red paint"],
            instructions="",
        )
        assert "with red paint" in out
        # no stray comma sequences from blanks
        assert ",," not in out

    def test_instructions_can_be_string_or_list(self):
        a = compose_scene_prompt(base_prompt="x", instructions="y")
        b = compose_scene_prompt(base_prompt="x", instructions=["y"])
        assert a == b


class TestComposeScenePresets:
    def test_golden_hour_modifier_lands_after_stacked_inputs(self):
        out = compose_scene_prompt(
            base_prompt="A river",
            prior_instructions=["stone bridge"],
            instructions="moss on the banks",
            preset="golden_hour",
        )
        i_moss = out.index("moss on the banks")
        i_modifier = out.lower().index("golden")
        assert i_moss < i_modifier

    def test_unknown_preset_raises(self):
        with pytest.raises(ValueError):
            compose_scene_prompt(base_prompt="x", preset="bogus")


class TestSceneNegative:
    def test_negative_prompt_blocks_cartoon_and_deepfake(self):
        assert "cartoon" in SCENE_NEGATIVE_PROMPT.lower()
        assert "deepfake" in SCENE_NEGATIVE_PROMPT.lower()
        assert "watermark" in SCENE_NEGATIVE_PROMPT.lower()
