"""Unit tests for the profile-picture prompt composer."""

import pytest

from flashback.profile_picture import compose_image_prompt, map_gender


class TestMapGender:
    def test_he_maps_to_male(self):
        assert map_gender("he") == "male"

    def test_she_maps_to_female(self):
        assert map_gender("she") == "female"

    def test_they_maps_to_non_binary(self):
        assert map_gender("they") == "non_binary"

    def test_none_maps_to_unspecified(self):
        assert map_gender(None) == "unspecified"

    def test_empty_string_maps_to_unspecified(self):
        assert map_gender("") == "unspecified"


class TestComposeImagePrompt:
    def test_contains_pixar_style(self):
        prompt = compose_image_prompt(name="Maya", gender=None)
        assert "Pixar-style" in prompt

    def test_contains_name(self):
        prompt = compose_image_prompt(name="Eleanor Vance", gender="she")
        assert "Eleanor Vance" in prompt

    def test_female_includes_female_character(self):
        prompt = compose_image_prompt(name="Maya", gender="she")
        assert "female character" in prompt

    def test_male_includes_male_character(self):
        prompt = compose_image_prompt(name="Robert", gender="he")
        assert "male character" in prompt

    def test_non_binary_includes_hint(self):
        prompt = compose_image_prompt(name="Alex", gender="they")
        assert "non-binary character" in prompt

    def test_unspecified_omits_gender_hint(self):
        prompt = compose_image_prompt(name="Jamie", gender=None)
        assert "male character" not in prompt
        assert "female character" not in prompt
        assert "non-binary character" not in prompt

    def test_relationship_included(self):
        prompt = compose_image_prompt(name="Nana", gender="she", relationship="grandmother")
        assert "grandmother" in prompt

    def test_relationship_omitted_when_none(self):
        prompt = compose_image_prompt(name="Maya", gender="she", relationship=None)
        assert "depicted as" not in prompt

    def test_user_instructions_appended(self):
        prompt = compose_image_prompt(
            name="Maya",
            gender="she",
            user_instructions="wearing a blue sari",
        )
        assert "wearing a blue sari" in prompt

    def test_blank_user_instructions_ignored(self):
        prompt_without = compose_image_prompt(name="Maya", gender="she")
        prompt_with_blank = compose_image_prompt(
            name="Maya", gender="she", user_instructions="   "
        )
        assert prompt_without == prompt_with_blank

    def test_no_text_watermarks_present(self):
        prompt = compose_image_prompt(name="Maya", gender=None)
        assert "no text no watermarks" in prompt
