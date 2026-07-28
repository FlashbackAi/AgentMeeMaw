"""Compiled context builders apply the preset + negative and respect caps."""

from __future__ import annotations

from flashback.artifacts.compose import (
    COVER_PORTRAIT_NEGATIVE_PROMPT,
    SCENE_NEGATIVE_PROMPT,
)
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


def test_storybook_pages_carry_editorial_fields_when_set() -> None:
    script = TributeScript(
        scenes=[
            Scene(
                moment_id="m0",
                caption="c0",
                accent="One · The Drop Ride",
                pull_quote="Hold on, here we go.",
                layout="hero",
            )
        ],
        opening_caption="open",
        closing_caption="close",
        message_text="Thank you.",
    )
    ctx = build_storybook_context(
        script=script,
        moments_by_id=_moments(1),
        preset="storybook",
        max_pages=9,
    )
    page = ctx["pages"][0]
    assert page["accent"] == "One · The Drop Ride"
    assert page["pull_quote"] == "Hold on, here we go."
    assert page["layout"] == "hero"


def test_storybook_page_accent_falls_back_to_time_anchor() -> None:
    script = _script(1)  # plain scenes, no accent emitted
    moments = {"m0": {"generation_prompt": "scene 0", "time_anchor": {"era": "1980s"}}}
    ctx = build_storybook_context(
        script=script, moments_by_id=moments, preset="storybook", max_pages=9
    )
    page = ctx["pages"][0]
    assert page["accent"] == "1980s"
    # Optional fields with no value are omitted entirely (clean degrade).
    assert "pull_quote" not in page
    assert "layout" not in page


def test_storybook_page_omits_accent_when_no_source() -> None:
    ctx = build_storybook_context(
        script=_script(1), moments_by_id=_moments(1), preset="storybook", max_pages=9
    )
    assert "accent" not in ctx["pages"][0]


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


def _confession_script() -> TributeScript:
    return TributeScript(
        scenes=[Scene(moment_id="m0", caption="He sold the house.")],
        opening_caption="For him.",
        closing_caption="The best I could ask for.",
        message_text="I love you, Dad.",
        cover_title="A Quiet Builder",
        cover_prompt="a concrete house rising in a village at dawn",
        defining_phrase="A man who spent himself so we'd never have to.",
        hero_line="He could have owned the valley. He chose a report card.",
    )


def test_cover_uses_reference_photo_relaxed_negative_and_lines() -> None:
    ctx = build_storybook_context(
        script=_confession_script(),
        moments_by_id={"m0": {"narrative": "He sold the house."}},
        preset="storybook",
        max_pages=9,
        cover_reference_s3_key="uploads/p/prime.jpg",
        deage_cover=True,
        defining_phrase="A man who spent himself so we'd never have to.",
        hero_line="He could have owned the valley. He chose a report card.",
    )
    cover = ctx["cover"]
    assert cover["reference_s3_key"] == "uploads/p/prime.jpg"
    assert cover["caption"] == "A man who spent himself so we'd never have to."
    assert cover["hero_line"] == (
        "He could have owned the valley. He chose a report card."
    )
    assert cover["negative"] == COVER_PORTRAIT_NEGATIVE_PROMPT
    assert "deepfake likeness" not in COVER_PORTRAIT_NEGATIVE_PROMPT
    assert "prime" in cover["prompt"].lower()
    assert "younger" in cover["prompt"].lower() or "de-age" in cover["prompt"].lower()
    # Page art is unaffected -- still the full scene negative incl. the ban.
    assert ctx["pages"][0]["negative"] == SCENE_NEGATIVE_PROMPT
    assert "deepfake likeness" in SCENE_NEGATIVE_PROMPT


def test_cover_without_reference_keeps_establishing_scene() -> None:
    ctx = build_storybook_context(
        script=_confession_script(),
        moments_by_id={"m0": {"narrative": "He sold the house."}},
        preset="storybook",
        max_pages=9,
    )
    cover = ctx["cover"]
    assert "reference_s3_key" not in cover
    # No defining_phrase arg, but the script carries one -> it wins over title.
    assert cover["caption"] == "A man who spent himself so we'd never have to."
    assert cover["negative"] == SCENE_NEGATIVE_PROMPT


def test_cover_caption_falls_back_to_title_when_no_defining_phrase() -> None:
    script = TributeScript(
        scenes=[Scene(moment_id="m0", caption="He sold the house.")],
        opening_caption="For him.",
        closing_caption="",
        message_text="",
        cover_title="A Quiet Builder",
    )
    ctx = build_storybook_context(
        script=script,
        moments_by_id={"m0": {"narrative": "He sold the house."}},
        preset="storybook",
        max_pages=9,
    )
    assert ctx["cover"]["caption"] == "A Quiet Builder"
