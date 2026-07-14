"""Deterministic composer: structured content -> prompt slot strings."""

from __future__ import annotations

from flashback.tribute.composer import compose_directives
from flashback.tribute.config_schema import (
    NEUTRAL_CAMPAIGN,
    CampaignConfig,
    ProfileConfig,
)


def _friend_profile() -> ProfileConfig:
    return ProfileConfig(
        id="pf-1",
        group_slug="friend",
        display_name="Friend",
        synonyms=("friend",),
        voice={
            "energy_words": ["playful", "teasing", "loyal"],
            "narrator_stance": "their partner-in-crime",
            "emotion_rule": "sincerity breaks through only at the very end",
            "never": ["meet my friend introductions", "eulogy tone"],
        },
        opener={
            "style": "open like the first line of a story told at every party",
            "examples": ["Nobody warned me about {name}.", "Some people get lucky. I got {name}."],
        },
        art={"mood_words": ["bright", "candid"], "avoid": ["posed", "solemn"]},
        fallback_opener="Some people get lucky. I got {name}.",
        fallback_closing="Thank you, {name}.",
        archetype_bank=[{"question": "Q?", "options": ["a", "b"]}],
        message_invitation_copy="Say the thing.",
        deage_cover=False,
        video_target_seconds=None,
        visual_theme_id="vt-profile",
        state="published",
        version=1,
    )


def _campaign(**overrides) -> CampaignConfig:
    base = dict(
        id="c-1",
        slug="friendship_day_2026",
        display_name="A Friendship Day Tribute",
        message_card_copy=None,
        archetype_extra_context="",
        video_target_seconds=None,
        featured=True,
        active_start=None,
        active_end=None,
        archetype_bank_override=None,
        deage_cover_override=None,
        visual_theme_id=None,
        closing_card_copy=None,
        state="published",
        version=1,
    )
    base.update(overrides)
    return CampaignConfig(**base)


def test_voice_block_golden() -> None:
    d = compose_directives(_friend_profile(), NEUTRAL_CAMPAIGN)
    assert d.voice_block == (
        "You are their partner-in-crime. "
        "Energy: playful, teasing, loyal. "
        "sincerity breaks through only at the very end. "
        "Never: meet my friend introductions; eulogy tone."
    )


def test_opener_style_golden() -> None:
    d = compose_directives(_friend_profile(), NEUTRAL_CAMPAIGN)
    assert d.opener_style == (
        "open like the first line of a story told at every party "
        "Examples of the register (adapt, never copy verbatim): "
        "Nobody warned me about {name}. | Some people get lucky. I got {name}."
    )


def test_art_mood_golden() -> None:
    d = compose_directives(_friend_profile(), NEUTRAL_CAMPAIGN)
    assert d.art_mood == (
        "Overall visual mood: bright, candid. Avoid: posed, solemn."
    )


def test_neutral_campaign_takes_profile_values() -> None:
    d = compose_directives(_friend_profile(), NEUTRAL_CAMPAIGN)
    assert d.deage_cover is False
    assert d.bank == [{"question": "Q?", "options": ["a", "b"]}]
    assert d.message_invitation_copy == "Say the thing."
    assert d.visual_theme_id == "vt-profile"
    assert d.fallback_opener == "Some people get lucky. I got {name}."


def test_campaign_overrides_win() -> None:
    campaign = _campaign(
        archetype_bank_override=[{"question": "FD?", "options": ["x", "y"]}],
        deage_cover_override=True,
        message_card_copy="Campaign copy.",
        visual_theme_id="vt-campaign",
    )
    d = compose_directives(_friend_profile(), campaign)
    assert d.bank == [{"question": "FD?", "options": ["x", "y"]}]
    assert d.deage_cover is True
    assert d.message_invitation_copy == "Campaign copy."
    assert d.visual_theme_id == "vt-campaign"


def test_explicit_false_override_beats_profile_true() -> None:
    profile = _friend_profile()
    profile = ProfileConfig(**{**profile.__dict__, "deage_cover": True})
    d = compose_directives(profile, _campaign(deage_cover_override=False))
    assert d.deage_cover is False


def test_deterministic() -> None:
    a = compose_directives(_friend_profile(), NEUTRAL_CAMPAIGN)
    b = compose_directives(_friend_profile(), NEUTRAL_CAMPAIGN)
    assert a == b


def test_empty_never_and_avoid_omit_clauses() -> None:
    profile = _friend_profile()
    voice = dict(profile.voice, never=[])
    art = dict(profile.art, avoid=[])
    profile = ProfileConfig(**{**profile.__dict__, "voice": voice, "art": art})
    d = compose_directives(profile, NEUTRAL_CAMPAIGN)
    assert "Never:" not in d.voice_block
    assert "Avoid:" not in d.art_mood
