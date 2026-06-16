"""Compiled context builders apply the preset + negative and respect caps."""

from __future__ import annotations

from flashback.artifacts.compose import SCENE_NEGATIVE_PROMPT
from flashback.tribute.artifact_context import (
    build_storybook_context,
    build_tribute_video_context,
)
from flashback.tribute.assembly import Scene, TributeScript


def _script(n: int) -> TributeScript:
    return TributeScript(
        scenes=[Scene(moment_id=f"m{i}", caption=f"c{i}") for i in range(n)],
        opening_caption="open",
        closing_caption="close",
        message_text="Thank you.",
    )


def _moments(n: int) -> dict:
    return {f"m{i}": {"generation_prompt": f"scene {i}"} for i in range(n)}


def test_video_context_durations_sum_near_target() -> None:
    ctx = build_tribute_video_context(
        script=_script(4),
        moments_by_id=_moments(4),
        preset="painterly_cinematic",
        target_duration_seconds=40,
    )
    assert len(ctx["scenes"]) == 4
    assert ctx["target_duration_seconds"] == 40
    assert all(s["negative"] == SCENE_NEGATIVE_PROMPT for s in ctx["scenes"])
    assert ctx["message_text"] == "Thank you."
    total = sum(s["duration_seconds"] for s in ctx["scenes"])
    assert 30 <= total <= 50  # ~10s/scene, rounded


def test_storybook_caps_pages_at_max_minus_cover() -> None:
    ctx = build_storybook_context(
        script=_script(20),
        moments_by_id=_moments(20),
        preset="storybook",
        max_pages=9,
    )
    assert len(ctx["pages"]) == 8  # 9 max - 1 cover
    assert ctx["message_page"]["text"] == "Thank you."
    assert ctx["max_pages"] == 9


def test_storybook_cover_uses_title_and_subtitle_without_prompt() -> None:
    # No cover_prompt → no dedicated cover image; caption falls back to opening.
    ctx = build_storybook_context(
        script=_script(3),
        moments_by_id=_moments(3),
        preset="storybook",
        max_pages=9,
        cover_subtitle="Mokshith",
    )
    assert ctx["cover"]["caption"] == "open"  # cover_title empty → opening line
    assert ctx["cover"]["subtitle"] == "Mokshith"
    assert "prompt" not in ctx["cover"]


def test_storybook_cover_composes_dedicated_prompt_when_present() -> None:
    script = TributeScript(
        scenes=[Scene(moment_id="m0", caption="c0")],
        opening_caption="open",
        closing_caption="close",
        message_text="Thank you.",
        cover_title="A Quiet Builder",
        cover_prompt="a wide dramatic dawn over the old workshop",
    )
    ctx = build_storybook_context(
        script=script,
        moments_by_id=_moments(1),
        preset="storybook",
        max_pages=9,
        cover_subtitle="Mokshith",
    )
    assert ctx["cover"]["caption"] == "A Quiet Builder"  # short title preferred
    assert ctx["cover"]["subtitle"] == "Mokshith"
    assert "a wide dramatic dawn over the old workshop" in ctx["cover"]["prompt"]
    assert ctx["cover"]["negative"] == SCENE_NEGATIVE_PROMPT
