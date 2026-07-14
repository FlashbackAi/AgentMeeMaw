"""Typed carriers + validation for the tribute CRM config tables.

The CRM writes structured *content* (JSONB), never prompts: a deterministic
composer (flashback.tribute.composer) assembles these fields into the fixed
prompt slots, so the guardrails stay in code and a content edit can never
break the render rules. Validators return human-readable error strings the
admin API surfaces as 422 bodies — the CRM shows them next to the field.

Spec: docs/superpowers/specs/2026-07-14-tribute-campaign-crm-design.md §3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from flashback.themes.archetype_llm import ArchetypeQuestion
from flashback.tribute.theme import TRIBUTE_DISPLAY_NAME

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class ProfileConfig:
    id: str
    group_slug: str
    display_name: str
    synonyms: tuple[str, ...]
    voice: dict
    opener: dict
    art: dict
    fallback_opener: str
    fallback_closing: str
    archetype_bank: list[dict] | None
    message_invitation_copy: str | None
    deage_cover: bool
    video_target_seconds: int | None
    visual_theme_id: str | None
    state: str
    version: int


@dataclass(frozen=True)
class CampaignConfig:
    id: str
    slug: str
    display_name: str
    message_card_copy: str | None
    archetype_extra_context: str
    video_target_seconds: int | None
    featured: bool
    active_start: date | None
    active_end: date | None
    archetype_bank_override: list[dict] | None
    deage_cover_override: bool | None
    visual_theme_id: str | None
    closing_card_copy: str | None
    state: str
    version: int


@dataclass(frozen=True)
class VisualThemeConfig:
    id: str
    slug: str
    display_name: str
    has_image: bool
    template_mime: str | None
    fonts: dict
    ink: dict
    audio_slug: str
    state: str
    version: int


# The year-round no-campaign default: pure wrapper-neutrality. Kept in code
# (not a seed row) so resolution can never come back empty.
NEUTRAL_CAMPAIGN = CampaignConfig(
    id="",
    slug="default",
    display_name=TRIBUTE_DISPLAY_NAME,
    message_card_copy=None,
    archetype_extra_context="",
    video_target_seconds=None,
    featured=False,
    active_start=None,
    active_end=None,
    archetype_bank_override=None,
    deage_cover_override=None,
    visual_theme_id=None,
    closing_card_copy=None,
    state="published",
    version=0,
)


def _is_str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(s, str) for s in v)


def _validate_bank(bank, field: str, errors: list[str]) -> None:
    if bank is None:
        return
    if not isinstance(bank, list) or not bank:
        errors.append(f"{field}: must be a non-empty list of questions or null")
        return
    for i, q in enumerate(bank, start=1):
        if not isinstance(q, dict):
            errors.append(f"{field}[{i}]: must be an object")
            continue
        if not isinstance(q.get("question"), str) or not q["question"].strip():
            errors.append(f"{field}[{i}]: question text is required")
        options = q.get("options")
        if not _is_str_list(options) or len([o for o in options or [] if o.strip()]) < 2:
            errors.append(f"{field}[{i}]: options needs at least 2 non-blank entries")


def validate_profile_payload(d: dict) -> list[str]:
    """Errors for a relationship-profile payload; empty list = valid."""
    errors: list[str] = []
    if not isinstance(d.get("group_slug"), str) or not d["group_slug"].strip():
        errors.append("group_slug: required")
    if not isinstance(d.get("display_name"), str) or not d["display_name"].strip():
        errors.append("display_name: required")
    if "synonyms" in d and not _is_str_list(d["synonyms"]):
        errors.append("synonyms: must be a list of strings")

    voice = d.get("voice")
    if not isinstance(voice, dict):
        errors.append("voice: required object")
    else:
        if not _is_str_list(voice.get("energy_words")) or not voice.get("energy_words"):
            errors.append("voice.energy_words: at least one word required")
        for key in ("narrator_stance", "emotion_rule"):
            if not isinstance(voice.get(key), str) or not voice[key].strip():
                errors.append(f"voice.{key}: required")
        if "never" in voice and not _is_str_list(voice["never"]):
            errors.append("voice.never: must be a list of strings")

    opener = d.get("opener")
    if not isinstance(opener, dict):
        errors.append("opener: required object")
    else:
        if not isinstance(opener.get("style"), str) or not opener["style"].strip():
            errors.append("opener.style: required")
        examples = opener.get("examples")
        if not _is_str_list(examples) or not examples:
            errors.append("opener.examples: at least one example required")
        else:
            for i, ex in enumerate(examples, start=1):
                if "{name}" not in ex:
                    errors.append(f"opener.examples[{i}]: must contain {{name}}")

    art = d.get("art")
    if not isinstance(art, dict):
        errors.append("art: required object")
    else:
        if not _is_str_list(art.get("mood_words")) or not art.get("mood_words"):
            errors.append("art.mood_words: at least one word required")
        if "avoid" in art and not _is_str_list(art["avoid"]):
            errors.append("art.avoid: must be a list of strings")

    for key in ("fallback_opener", "fallback_closing"):
        v = d.get(key)
        if not isinstance(v, str) or not v.strip():
            errors.append(f"{key}: required")
        elif "{name}" not in v:
            errors.append(f"{key}: must contain {{name}}")

    _validate_bank(d.get("archetype_bank"), "archetype_bank", errors)

    if d.get("video_target_seconds") is not None and not isinstance(
        d["video_target_seconds"], int
    ):
        errors.append("video_target_seconds: must be an integer or null")
    return errors


def validate_campaign_payload(d: dict) -> list[str]:
    """Errors for a campaign payload; empty list = valid."""
    errors: list[str] = []
    if not isinstance(d.get("slug"), str) or not d["slug"].strip():
        errors.append("slug: required")
    if not isinstance(d.get("display_name"), str) or not d["display_name"].strip():
        errors.append("display_name: required")
    _validate_bank(
        d.get("archetype_bank_override"), "archetype_bank_override", errors
    )
    for key in ("active_start", "active_end"):
        v = d.get(key)
        if v is not None and not isinstance(v, (str, date)):
            errors.append(f"{key}: must be an ISO date or null")
    start, end = d.get("active_start"), d.get("active_end")
    if bool(start) != bool(end):
        errors.append("active_start/active_end: set both or neither")
    if d.get("featured") and not (start and end):
        errors.append("featured: a featured campaign needs a window")
    if d.get("video_target_seconds") is not None and not isinstance(
        d["video_target_seconds"], int
    ):
        errors.append("video_target_seconds: must be an integer or null")
    return errors


def validate_ink(ink: dict) -> list[str]:
    """Errors for a visual theme's ink object ({main_fill, eyebrow_fill})."""
    errors: list[str] = []
    if not isinstance(ink, dict):
        return ["ink: required object"]
    for key in ("main_fill", "eyebrow_fill"):
        v = ink.get(key)
        if not isinstance(v, str) or not _HEX_RE.match(v):
            errors.append(f"ink.{key}: must be a #rrggbb hex color")
    return errors


def bank_to_archetype_questions(bank: list[dict]) -> list[ArchetypeQuestion]:
    """A stored JSONB bank as ArchetypeQuestion objects (no LLM call).

    Ids are positional against the ORIGINAL bank order (q{n} / q{n}_o{m}) so
    answers stored against a bank keep resolving even when a thin question
    (fewer than 2 non-blank options) is dropped from the served list.
    """
    out: list[ArchetypeQuestion] = []
    for q_idx, entry in enumerate(bank or [], start=1):
        text = (entry.get("question") or "").strip()
        if not text:
            continue
        options = [
            {"option_id": f"q{q_idx}_o{o_idx}", "label": label}
            for o_idx, label in enumerate(entry.get("options") or [], start=1)
            if isinstance(label, str) and label.strip()
        ]
        if len(options) < 2:
            continue
        out.append(
            ArchetypeQuestion(question_id=f"q{q_idx}", text=text, options=options)
        )
    return out
