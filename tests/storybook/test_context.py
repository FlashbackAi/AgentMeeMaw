"""StorybookRenderContext — the Postgres-authoritative render context."""

from __future__ import annotations

from flashback.storybook.context import (
    CONTEXT_KEY,
    StorybookRenderContext,
    build_context_dict,
)


def _dict(**over):
    base = dict(
        collection="childhood",
        subject_name="C",
        relationship="Grand Father",
        gt_context="gt",
        gender="male",
        moments=[{"title": "t", "narrative": "n"}],
        pdf_put_url="p",
        cover_put_url="c",
        page_put_urls=["u"] * 7,
        anchor_photo_get_url="a",
        composed_at="2026-07-03T00:00:00Z",
    )
    base.update(over)
    return build_context_dict(**base)


def test_context_key() -> None:
    assert CONTEXT_KEY == "storybook"


def test_round_trip() -> None:
    ctx = StorybookRenderContext.from_dict(
        _dict(), storybook_id="s", person_id="p1"
    )
    assert ctx.storybook_id == "s"
    assert ctx.person_id == "p1"
    assert ctx.collection == "childhood"
    assert len(ctx.page_put_urls) == 7
    assert ctx.anchor_photo_get_url == "a"
    assert ctx.composed_at == "2026-07-03T00:00:00Z"
    assert ctx.reuse_script is False
    assert ctx.edit_instructions == []


def test_edit_and_reuse_flags_round_trip() -> None:
    d = _dict(edit_instructions=["warmer"], reuse_script=True)
    ctx = StorybookRenderContext.from_dict(d, storybook_id="s", person_id="p")
    assert ctx.edit_instructions == ["warmer"]
    assert ctx.reuse_script is True


def test_missing_optionals_default() -> None:
    d = _dict()
    d.pop("anchor_photo_get_url")
    d.pop("gender")
    ctx = StorybookRenderContext.from_dict(d, storybook_id="s", person_id="p")
    assert ctx.anchor_photo_get_url == ""
    assert ctx.gender is None
