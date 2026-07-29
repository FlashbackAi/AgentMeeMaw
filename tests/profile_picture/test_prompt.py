"""Unit tests for the profile-picture prompt composer."""

import pytest

from flashback.profile_picture import (
    NEGATIVE_PROMPT,
    REPRESENTATIONAL_NEGATIVE_PROMPT,
    compose_image_prompt,
    compose_representational_prompt,
    map_gender,
)


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
    def test_contains_rdr2_style(self):
        prompt = compose_image_prompt(name="Maya", gender=None)
        assert "Painterly semi-realistic portrait" in prompt
        assert "Red Dead Redemption 2" in prompt

    def test_contains_name(self):
        prompt = compose_image_prompt(name="Eleanor Vance", gender="she")
        assert "Eleanor Vance" in prompt

    def test_female_includes_female_subject(self):
        prompt = compose_image_prompt(name="Maya", gender="she")
        assert "female subject" in prompt

    def test_male_includes_male_subject(self):
        prompt = compose_image_prompt(name="Robert", gender="he")
        assert "male subject" in prompt

    def test_non_binary_includes_hint(self):
        prompt = compose_image_prompt(name="Alex", gender="they")
        assert "non-binary subject" in prompt

    def test_unspecified_omits_gender_hint(self):
        prompt = compose_image_prompt(name="Jamie", gender=None)
        assert "male subject" not in prompt
        assert "female subject" not in prompt
        assert "non-binary subject" not in prompt

    def test_relationship_included(self):
        prompt = compose_image_prompt(name="Nana", gender="she", relationship="grandmother")
        assert "grandmother" in prompt

    def test_relationship_uses_ordinary_contemporary_anchor(self):
        # Stronger relationship anchor: forces the model to read the
        # relationship as an ordinary modern role, not a mythological
        # archetype. Part of the deity-name fix (see CLAUDE.md §1).
        prompt = compose_image_prompt(name="Nana", gender="she", relationship="grandmother")
        assert "ordinary contemporary grandmother" in prompt
        assert "modern everyday clothing" in prompt

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

    def test_ordinary_person_anchor_always_present(self):
        # Anchor must fire on every portrait, even when relationship is
        # absent — the failure mode (deity rendering for names like
        # "Krishna" / "Jesus" / "Buddha") is driven by the name alone.
        for case in [
            {"name": "Krishna", "gender": "he", "relationship": None},
            {"name": "Krishna", "gender": "he", "relationship": "friend"},
            {"name": "Jesus", "gender": "he", "relationship": "uncle"},
            {"name": "Buddha", "gender": "he", "relationship": None},
            {"name": "Athena", "gender": "she", "relationship": "mother"},
        ]:
            prompt = compose_image_prompt(**case)
            assert "ordinary contemporary person" in prompt, case
            assert "not a religious or mythological figure" in prompt, case

    def test_anchor_sits_near_the_name(self):
        # Spatial proximity matters for prompt-conditioning models — the
        # anchor needs to land before later style modifiers can drift.
        prompt = compose_image_prompt(name="Krishna", gender="he")
        name_idx = prompt.index("Krishna")
        anchor_idx = prompt.index("ordinary contemporary person")
        style_idx = prompt.index("Red Dead Redemption 2")
        assert name_idx < anchor_idx < style_idx


class TestNegativePromptBlocksDeityIconography:
    """Regression guards for the deity-name failure mode.

    These pin the load-bearing negative terms so a future tidy-up of
    NEGATIVE_PROMPT can't silently remove the iconography block.
    """

    def test_blocks_deity_and_mythological_figures(self):
        for term in [
            "religious deity",
            "god",
            "goddess",
            "divine being",
            "mythological figure",
            "holy avatar",
        ]:
            assert term in NEGATIVE_PROMPT, term

    def test_blocks_deity_visual_tells(self):
        for term in [
            "halo",
            "divine aura",
            "multi-armed figure",
            "multiple arms",
            "blue-skinned deity",
            "peacock-feather crown",
            "deity holding a flute",
            "lotus throne",
        ]:
            assert term in NEGATIVE_PROMPT, term

    def test_does_not_block_ordinary_cultural_attire(self):
        # We do NOT want to suppress legitimate cultural attire on
        # ordinary people. A tilaka, sari, kurta, kippah, hijab,
        # cross-necklace, etc. on an everyday person is fine — only
        # the deity-specific tells are blocked. Pinning the absence
        # of these terms prevents over-restrictive future edits.
        for term in ["tilaka", "sari", "kurta", "hijab", "kippah", "cross necklace"]:
            assert term not in NEGATIVE_PROMPT.lower(), term

    def test_still_blocks_original_terms(self):
        # The deity additions are additive — original terms stay.
        for term in [
            "photograph",
            "deepfake",
            "Pixar 3D look",
            "watermark",
        ]:
            assert term in NEGATIVE_PROMPT, term

    def test_instructions_list_stacks_in_order(self):
        prompt = compose_image_prompt(
            name="Maya",
            gender="she",
            user_instructions=["he has glasses", "and a Rolls Royce"],
        )
        glasses_idx = prompt.index("he has glasses")
        rolls_idx = prompt.index("and a Rolls Royce")
        assert glasses_idx < rolls_idx

    def test_instructions_list_skips_blanks(self):
        prompt = compose_image_prompt(
            name="Maya",
            gender="she",
            user_instructions=["", "  ", "wearing a hat"],
        )
        assert "wearing a hat" in prompt

    def test_instructions_empty_list_equivalent_to_none(self):
        a = compose_image_prompt(name="Maya", gender="she")
        b = compose_image_prompt(name="Maya", gender="she", user_instructions=[])
        assert a == b


class TestComposeRepresentationalPrompt:
    """No-photo onboarding fallback — design 2026-07-29."""

    def test_always_from_behind_and_faceless(self):
        prompt = compose_representational_prompt(gender="she", relationship="friend")
        assert "seen from behind" in prompt
        assert "face never visible" in prompt
        assert "storybook illustration" in prompt

    def test_gender_shapes_the_figure(self):
        assert "female figure" in compose_representational_prompt(
            gender="she", relationship="friend"
        )
        assert "male figure" in compose_representational_prompt(
            gender="he", relationship="friend"
        )

    def test_neutral_gender_gets_androgynous_figure(self):
        prompt = compose_representational_prompt(gender="they", relationship="friend")
        assert "male figure" not in prompt
        assert "female figure" not in prompt
        assert "androgynous silhouette" in prompt

    def test_grandparent_becomes_elderly_figure(self):
        prompt = compose_representational_prompt(
            gender="he", relationship="grandfather"
        )
        assert "elderly male figure" in prompt
        assert "grandfather's gentle posture" in prompt

    def test_parent_gets_middle_years_clause(self):
        prompt = compose_representational_prompt(gender="she", relationship="mother")
        assert "mother's warm unhurried posture" in prompt
        assert "middle years" in prompt

    def test_peer_dresses_casually(self):
        prompt = compose_representational_prompt(gender="she", relationship="friend")
        assert "dressed casually like a close friend" in prompt

    def test_unknown_relationship_falls_back_to_everyday_clothing(self):
        prompt = compose_representational_prompt(gender="she", relationship="mentor")
        assert "simple everyday clothing" in prompt

    def test_name_is_never_needed_or_present(self):
        # The composer doesn't even accept a name — the prompt must carry
        # no name-driven priors. Guard the scene text stays name-free.
        prompt = compose_representational_prompt(gender=None, relationship=None)
        assert "portrait" not in prompt.lower()

    def test_negative_prompt_forbids_faces_and_crowds(self):
        for term in [
            "visible face",
            "frontal view",
            "multiple people",
            "photograph",
            "religious deity iconography",
            "watermark",
        ]:
            assert term in REPRESENTATIONAL_NEGATIVE_PROMPT, term
