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
) -> dict[str, Any]:
    """Compile the storybook context (keyed under 'storybook').

    Cover + up to (max_pages - 1) content pages. The contributor message is
    the final page.
    """
    content_budget = max(1, max_pages - 1)
    pages: list[dict[str, Any]] = []
    for s in script.scenes[:content_budget]:
        moment = moments_by_id.get(s.moment_id, {})
        prompt = compose_scene_prompt(
            base_prompt=_scene_base_prompt(moment),
            preset=preset,
            ground_truth_context=ground_truth_context,
        )
        pages.append(
            {
                "moment_id": s.moment_id,
                "prompt": prompt,
                "negative": SCENE_NEGATIVE_PROMPT,
                "caption": s.caption,
            }
        )
    return {
        "cover": {
            "caption": script.opening_caption,
            "style_preset": preset,
        },
        "pages": pages,
        "message_page": {"text": script.message_text},
        "closing_caption": script.closing_caption,
        "style_preset": preset,
        "max_pages": max_pages,
        "negative_prompt": SCENE_NEGATIVE_PROMPT,
        "composed_at": datetime.now(timezone.utc).isoformat(),
    }
