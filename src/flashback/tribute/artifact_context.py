"""Build the compiled generation contexts for tribute video + storybook.

Per-scene prompts reuse the existing scene composer + negative prompt, so
the painterly-realism register and the no-photorealism/no-deepfake bans
(SCENE_NEGATIVE_PROMPT) apply to every scene/page (spec section 2). The
shapes here are what Node's compiled renderer reads from
``tributes.latest_generation_context[artifact_kind]``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flashback.artifacts.compose import SCENE_NEGATIVE_PROMPT, compose_scene_prompt
from flashback.tribute.assembly import TributeScript

# Node now renders storybook captions in a designed HTML/CSS editorial layout
# rather than baking text into the image, so the page art no longer reserves a
# blank text column. Compose each page as a complete, full-frame painterly
# image that fills the frame edge to edge, with the subject and key elements
# kept off the extreme edges so the image survives Node's crop to framed insets
# and full-bleed slots.
STORYBOOK_PAGE_COMPOSITION = (
    "composition: a complete, full-frame painterly image that fills the whole "
    "frame edge to edge with no reserved blank space for text; keep the subject "
    "and key elements away from the extreme edges so the image survives cropping "
    "to framed insets and full-bleed slots"
)


def _time_anchor_label(time_anchor: Any) -> str:
    """Flat fallback accent label from a moment's ``time_anchor`` JSONB.

    Used only when the assembler omitted an ``accent`` for a scene. The
    JSONB shape is ``{year?, decade?, life_period?, era?}`` (migration 0001);
    prefer the most human-readable field present.
    """
    if not isinstance(time_anchor, dict):
        return ""
    for key in ("era", "life_period", "decade", "year"):
        val = time_anchor.get(key)
        if val:
            return str(val).strip()
    return ""


def _scene_base_prompt(moment: dict[str, Any]) -> str:
    """Prefer the moment's LLM-emitted generation_prompt; fall back to text."""
    base = (moment.get("generation_prompt") or "").strip()
    if base:
        return base
    # No stored scene prompt (older moment) -- ground on its own text.
    return (moment.get("narrative") or moment.get("title") or "").strip()


def build_tribute_video_context(
    *,
    script: TributeScript,
    moments_by_id: dict[str, dict[str, Any]],
    preset: str,
    target_duration_seconds: int,
    ground_truth_context: str | None = None,
) -> dict[str, Any]:
    """Compile the tribute-video context (keyed under 'tribute_video')."""
    n = max(1, len(script.scenes))
    per_scene = max(2, round(target_duration_seconds / n))
    scenes: list[dict[str, Any]] = []
    for s in script.scenes:
        moment = moments_by_id.get(s.moment_id, {})
        prompt = compose_scene_prompt(
            base_prompt=_scene_base_prompt(moment),
            preset=preset,
            ground_truth_context=ground_truth_context,
        )
        scenes.append(
            {
                "moment_id": s.moment_id,
                "prompt": prompt,
                "negative": SCENE_NEGATIVE_PROMPT,
                "caption": s.caption,
                "duration_seconds": per_scene,
            }
        )
    return {
        "scenes": scenes,
        "opening_caption": script.opening_caption,
        "message_text": script.message_text,
        "closing_caption": script.closing_caption,
        "style_preset": preset,
        "target_duration_seconds": target_duration_seconds,
        "negative_prompt": SCENE_NEGATIVE_PROMPT,
        "composed_at": datetime.now(timezone.utc).isoformat(),
    }


def build_storybook_context(
    *,
    script: TributeScript,
    moments_by_id: dict[str, dict[str, Any]],
    preset: str,
    max_pages: int,
    ground_truth_context: str | None = None,
    cover_subtitle: str | None = None,
) -> dict[str, Any]:
    """Compile the storybook context (keyed under 'storybook').

    Cover + up to (max_pages - 1) content pages. The contributor message is
    the final page.

    The cover carries a dramatic dedicated image when the assembler emitted a
    ``cover_prompt`` (composed with the preset + negative like any scene); Node
    falls back to the first content still when ``cover.prompt`` is absent. The
    cover ``caption`` is the short ``cover_title`` (falling back to the opening
    line), with the subject name as an optional ``subtitle``.
    """
    content_budget = max(1, max_pages - 1)
    pages: list[dict[str, Any]] = []
    for s in script.scenes[:content_budget]:
        moment = moments_by_id.get(s.moment_id, {})
        prompt = compose_scene_prompt(
            base_prompt=_scene_base_prompt(moment),
            instructions=STORYBOOK_PAGE_COMPOSITION,
            preset=preset,
            ground_truth_context=ground_truth_context,
        )
        page: dict[str, Any] = {
            "moment_id": s.moment_id,
            "prompt": prompt,
            "negative": SCENE_NEGATIVE_PROMPT,
            "caption": s.caption,
        }
        # Optional editorial fields -- present only when meaningful, so Node's
        # renderer degrades cleanly (auto-alternates layout, no quote breather)
        # when a beat omits them.
        accent = (s.accent or _time_anchor_label(moment.get("time_anchor"))).strip()
        if accent:
            page["accent"] = accent
        if s.pull_quote:
            page["pull_quote"] = s.pull_quote
        if s.layout:
            page["layout"] = s.layout
        pages.append(page)
    cover: dict[str, Any] = {
        "caption": (script.cover_title or script.opening_caption or "").strip(),
        "subtitle": (cover_subtitle or "").strip(),
        "style_preset": preset,
    }
    cover_prompt = (script.cover_prompt or "").strip()
    if cover_prompt:
        cover["prompt"] = compose_scene_prompt(
            base_prompt=cover_prompt,
            preset=preset,
            ground_truth_context=ground_truth_context,
        )
        cover["negative"] = SCENE_NEGATIVE_PROMPT
    return {
        "cover": cover,
        "pages": pages,
        "message_page": {"text": script.message_text},
        "closing_caption": script.closing_caption,
        "style_preset": preset,
        "max_pages": max_pages,
        "negative_prompt": SCENE_NEGATIVE_PROMPT,
        "composed_at": datetime.now(timezone.utc).isoformat(),
    }
