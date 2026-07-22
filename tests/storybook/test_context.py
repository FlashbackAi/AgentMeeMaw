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


def test_context_user_curated_defaults_false_on_old_dicts() -> None:
    """A context written before this feature deserializes unchanged."""
    ctx = StorybookRenderContext.from_dict(
        {"collection": "childhood", "subject_name": "Dad"},
        storybook_id="sb1",
        person_id="p1",
    )
    assert ctx.user_curated is False


def test_context_round_trips_user_curated_and_ids() -> None:
    d = _dict(
        moments=[{"id": "m-1", "title": "t", "narrative": "n",
                  "life_period": "", "time_anchor": None}],
        user_curated=True,
    )
    assert d["user_curated"] is True
    ctx = StorybookRenderContext.from_dict(
        d, storybook_id="sb1", person_id="p1"
    )
    assert ctx.user_curated is True
    assert ctx.moments[0]["id"] == "m-1"


def test_context_roundtrips_new_gender_fields() -> None:
    d = build_context_dict(
        collection="friends", subject_name="Meera", relationship="friend",
        gt_context="", gender="she", contributor_gender="she",
        people=[{"name": "Aarav", "relationship": "her brother", "gender": "male"}],
        moments=[], pdf_put_url="", cover_put_url="", page_put_urls=[],
    )
    ctx = StorybookRenderContext.from_dict(d, storybook_id="s", person_id="p")
    assert ctx.contributor_gender == "she"
    assert ctx.people == [{"name": "Aarav", "relationship": "her brother", "gender": "male"}]


def test_context_old_dict_defaults_new_fields() -> None:
    ctx = StorybookRenderContext.from_dict(
        {"collection": "friends", "subject_name": "Meera"},
        storybook_id="s", person_id="p")
    assert ctx.contributor_gender is None
    assert ctx.people == []
