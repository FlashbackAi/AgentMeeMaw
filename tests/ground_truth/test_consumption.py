from flashback.artifacts.compose import compose_scene_prompt
from flashback.profile_picture.prompt import compose_image_prompt
from flashback.response_generator.context import render_turn_context
from flashback.response_generator.schema import TurnContext


def test_portrait_prompt_carries_ground_truth_descriptors():
    prompt = compose_image_prompt(
        name="Ishita", gender="she", relationship="grandmother",
        ground_truth_context=(
            "from Karimnagar, Telangana, India, born in the 1950s, "
            "typically wearing cotton saree"
        ),
    )
    assert "from Karimnagar, Telangana, India" in prompt
    assert "cotton saree" in prompt
    # Existing recipe is untouched
    assert "Red Dead Redemption 2 character art" in prompt


def test_portrait_prompt_unchanged_without_ground_truth():
    a = compose_image_prompt(name="Ishita", gender="she")
    b = compose_image_prompt(name="Ishita", gender="she",
                             ground_truth_context=None)
    assert a == b


def test_scene_prompt_appends_setting_context():
    prompt = compose_scene_prompt(
        base_prompt="A wood-paneled kitchen at dawn.",
        ground_truth_context="Setting context: rural Telangana, 1960s era.",
    )
    assert prompt.startswith("A wood-paneled kitchen at dawn.")
    assert "Setting context: rural Telangana, 1960s era." in prompt


def test_turn_context_renders_subject_ground_truth_block():
    ctx = TurnContext(
        person_name="Ishita", intent="story",
        emotional_temperature="medium",
        ground_truth_block="region: Karimnagar, Telangana, India",
    )
    rendered = render_turn_context(ctx)
    assert "<subject_ground_truth>" in rendered
    assert "Karimnagar" in rendered


def test_turn_context_omits_block_when_empty():
    ctx = TurnContext(
        person_name="Ishita", intent="story",
        emotional_temperature="medium",
    )
    assert "<subject_ground_truth>" not in render_turn_context(ctx)
