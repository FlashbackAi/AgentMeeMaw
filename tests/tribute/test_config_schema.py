"""Typed config carriers + payload validation (Task 2, plan 2026-07-14)."""

from __future__ import annotations

import copy

from flashback.tribute.config_schema import (
    NEUTRAL_CAMPAIGN,
    bank_to_archetype_questions,
    validate_campaign_payload,
    validate_profile_payload,
)

# Mirrors the seeded friend profile's shapes (kept inline: tests must not
# read the migration).
VALID_PROFILE = {
    "group_slug": "friend",
    "display_name": "Friend",
    "synonyms": ["friend", "best friend"],
    "voice": {
        "energy_words": ["playful", "teasing"],
        "narrator_stance": "their partner-in-crime",
        "emotion_rule": "warmth lives inside the jokes",
        "never": ["meet my friend introductions"],
    },
    "opener": {
        "style": "open like a story told at every party",
        "examples": ["Nobody warned me about {name}."],
    },
    "art": {"mood_words": ["bright", "candid"], "avoid": ["posed"]},
    "fallback_opener": "Some people get lucky. I got {name}.",
    "fallback_closing": "Thank you, {name}.",
    "archetype_bank": [
        {"question": "How did they meet?", "options": ["School", "College"]},
    ],
    "message_invitation_copy": None,
    "deage_cover": False,
    "video_target_seconds": None,
}

VALID_CAMPAIGN = {
    "slug": "friendship_day_2026",
    "display_name": "A Friendship Day Tribute",
    "message_card_copy": "Say the thing.",
    "archetype_extra_context": "This is a Friendship Day tribute.",
    "video_target_seconds": 45,
    "featured": True,
    "active_start": "2026-07-28",
    "active_end": "2026-08-03",
    "archetype_bank_override": None,
    "deage_cover_override": None,
    "visual_theme_id": None,
    "closing_card_copy": None,
}


def test_valid_profile_passes() -> None:
    assert validate_profile_payload(VALID_PROFILE) == []


def test_valid_campaign_passes() -> None:
    assert validate_campaign_payload(VALID_CAMPAIGN) == []


def test_opener_example_missing_name_placeholder() -> None:
    bad = copy.deepcopy(VALID_PROFILE)
    bad["opener"]["examples"] = ["Nobody warned me about him."]
    errors = validate_profile_payload(bad)
    assert any("{name}" in e and "opener" in e for e in errors)


def test_fallback_missing_name_placeholder() -> None:
    bad = copy.deepcopy(VALID_PROFILE)
    bad["fallback_opener"] = "Meet my friend."
    errors = validate_profile_payload(bad)
    assert any("fallback_opener" in e for e in errors)


def test_bank_question_needs_two_options() -> None:
    bad = copy.deepcopy(VALID_PROFILE)
    bad["archetype_bank"] = [{"question": "Only one?", "options": ["Yes"]}]
    errors = validate_profile_payload(bad)
    assert any("options" in e for e in errors)


def test_voice_requires_energy_words() -> None:
    bad = copy.deepcopy(VALID_PROFILE)
    bad["voice"]["energy_words"] = []
    errors = validate_profile_payload(bad)
    assert any("energy_words" in e for e in errors)


def test_campaign_bank_override_validated() -> None:
    bad = copy.deepcopy(VALID_CAMPAIGN)
    bad["archetype_bank_override"] = [{"question": "", "options": ["a", "b"]}]
    errors = validate_campaign_payload(bad)
    assert any("question" in e for e in errors)


def test_bank_to_archetype_questions_ids_and_options() -> None:
    bank = [
        {"question": "Q one?", "options": ["A", "B", "C", "D"]},
        {"question": "Q two?", "options": ["E", "F"]},
    ]
    qs = bank_to_archetype_questions(bank)
    assert [q.question_id for q in qs] == ["q1", "q2"]
    assert qs[0].text == "Q one?"
    assert qs[0].options[0] == {"option_id": "q1_o1", "label": "A"}
    assert qs[1].options[1] == {"option_id": "q2_o2", "label": "F"}


def test_bank_to_archetype_questions_skips_thin_questions() -> None:
    bank = [
        {"question": "Thin", "options": ["only", ""]},
        {"question": "Fine", "options": ["a", "b"]},
    ]
    qs = bank_to_archetype_questions(bank)
    # "Thin" collapses to 1 non-blank option -> dropped; ids stay positional
    # against the ORIGINAL bank order so stored answers keep resolving.
    assert len(qs) == 1
    assert qs[0].question_id == "q2"
    assert qs[0].text == "Fine"


def test_neutral_campaign_constants() -> None:
    assert NEUTRAL_CAMPAIGN.slug == "default"
    assert NEUTRAL_CAMPAIGN.display_name == "A Tribute"
    assert NEUTRAL_CAMPAIGN.archetype_bank_override is None
    assert NEUTRAL_CAMPAIGN.deage_cover_override is None
