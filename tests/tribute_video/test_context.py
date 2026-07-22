"""RenderContext — the Postgres-authoritative tribute video render context."""

from __future__ import annotations

from flashback.tribute_video.context import (
    CONTEXT_KEY,
    RenderContext,
    build_context_dict,
)


def _dict(**over):
    base = dict(
        subject_name="Meera",
        relationship="friend",
        gt_context="",
        candidates=[],
        video_put_url="",
        pdf_put_url="",
    )
    base.update(over)
    return build_context_dict(**base)


def test_context_key() -> None:
    assert CONTEXT_KEY == "tribute_video"


def test_tribute_context_roundtrips_gender() -> None:
    d = build_context_dict(subject_name="Meera", relationship="friend",
                           gt_context="", candidates=[],
                           gender="she", contributor_gender="she",
                           video_put_url="", pdf_put_url="")
    ctx = RenderContext.from_dict(d, tribute_id="t", person_id="p")
    assert ctx.gender == "she" and ctx.contributor_gender == "she"


def test_tribute_context_old_dict_defaults_none() -> None:
    """A context written before this feature deserializes unchanged."""
    ctx = RenderContext.from_dict({"subject_name": "Meera"}, tribute_id="t",
                                  person_id="p")
    assert ctx.gender is None and ctx.contributor_gender is None


def test_gender_absent_from_new_build_defaults_none() -> None:
    d = _dict()
    ctx = RenderContext.from_dict(d, tribute_id="t", person_id="p")
    assert ctx.gender is None
    assert ctx.contributor_gender is None
