"""Deterministic composition: structured CRM content -> assembler prompt slots.

No LLM here, by design (spec §2.5): what the content person typed is exactly
what steers the model, wrapped by the fixed guardrail skeleton in
flashback.tribute_video.assembler. The campaign->profile override chain is
also resolved here so every touchpoint applies the same precedence.
"""

from __future__ import annotations

from dataclasses import dataclass

from flashback.tribute.config_schema import CampaignConfig, ProfileConfig
from flashback.tribute.opener_presets import OPENER_PRESET_BY_SLUG


@dataclass(frozen=True)
class ComposedDirectives:
    voice_block: str
    opener_style: str
    art_mood: str
    narrative_block: str
    fallback_opener: str
    fallback_closing: str
    deage_cover: bool
    message_invitation_copy: str | None
    bank: list[dict] | None
    visual_theme_id: str | None


def _voice_block(voice: dict) -> str:
    parts = [
        f"You are {voice.get('narrator_stance', '').strip()}.",
        f"Energy: {', '.join(voice.get('energy_words') or [])}.",
        f"{voice.get('emotion_rule', '').strip()}.",
    ]
    never = [n.strip() for n in (voice.get("never") or []) if n.strip()]
    if never:
        parts.append(f"Never: {'; '.join(never)}.")
    return " ".join(p for p in parts if p != ".")


def _opener_style(opener: dict) -> str:
    # A chosen preset (opener.preset slug) supplies the style + examples from
    # the agent catalog; else fall back to the profile's free-text authoring.
    preset = OPENER_PRESET_BY_SLUG.get((opener.get("preset") or "").strip())
    if preset:
        style = preset["style"]
        examples = list(preset["examples"])
    else:
        style = (opener.get("style") or "").strip()
        examples = [e.strip() for e in (opener.get("examples") or []) if e.strip()]
    if not examples:
        return style
    return (
        f"{style} Examples of the register (adapt, never copy verbatim): "
        + " | ".join(examples)
    )


def _art_mood(art: dict) -> str:
    mood = f"Overall visual mood: {', '.join(art.get('mood_words') or [])}."
    avoid = [a.strip() for a in (art.get("avoid") or []) if a.strip()]
    if avoid:
        mood += f" Avoid: {', '.join(avoid)}."
    return mood


def _narrative_block(narrative: dict) -> str:
    """Compose the FRAMING block the assembler injects. Empty when nothing is
    authored, so the assembler falls back to its memorial default.

    Free-text by design: the author describes the audience / arc / throughline
    in prose, and the code just injects it -- so a new occasion (Mother's Day,
    a retirement, an anniversary) is pure CRM config, no code change."""
    audience = (narrative.get("audience") or "").strip()
    arc = (narrative.get("arc") or "").strip()
    throughline = (narrative.get("throughline") or "").strip()
    lines = []
    if audience:
        lines.append(f"- AUDIENCE: {audience}")
    if arc:
        lines.append(f"- ARC: {arc}")
    if throughline:
        lines.append(f"- THROUGHLINE: {throughline}")
    if not lines:
        return ""
    return "FRAMING -- who this is for and how it's shaped:\n" + "\n".join(lines)


def compose_directives(
    profile: ProfileConfig, campaign: CampaignConfig
) -> ComposedDirectives:
    """Assemble the slot strings + resolve the campaign->profile overrides."""
    deage = (
        campaign.deage_cover_override
        if campaign.deage_cover_override is not None
        else profile.deage_cover
    )
    return ComposedDirectives(
        voice_block=_voice_block(profile.voice),
        opener_style=_opener_style(profile.opener),
        art_mood=_art_mood(profile.art),
        # Occasion wins over relationship: a Friendship Day campaign frames the
        # story as a friendship regardless of which relationship it targets.
        # Empty override inherits the profile default (itself -> memorial default).
        narrative_block=_narrative_block(
            campaign.narrative_override or profile.narrative),
        fallback_opener=profile.fallback_opener,
        fallback_closing=profile.fallback_closing,
        deage_cover=bool(deage),
        message_invitation_copy=(
            campaign.message_card_copy or profile.message_invitation_copy
        ),
        bank=campaign.archetype_bank_override or profile.archetype_bank,
        visual_theme_id=campaign.visual_theme_id or profile.visual_theme_id,
    )
