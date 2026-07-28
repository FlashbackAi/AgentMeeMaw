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


def test_narrative_order_reverses_to_telling_order() -> None:
    """The fetch hands back newest-extracted first; a story starts at the start."""
    from flashback.tribute_video.context import order_candidates_for_narrative

    newest_first = [{"id": "c", "time_anchor": None},
                    {"id": "b", "time_anchor": None},
                    {"id": "a", "time_anchor": None}]
    assert [m["id"] for m in order_candidates_for_narrative(newest_first)] == \
        ["a", "b", "c"]


def test_narrative_order_uses_years_when_most_are_anchored() -> None:
    from flashback.tribute_video.context import order_candidates_for_narrative

    cands = [{"id": "late", "time_anchor": {"year": 2019}},
             {"id": "mid", "time_anchor": {"year": 2011}},
             {"id": "early", "time_anchor": {"year": 2004}},
             {"id": "undated", "time_anchor": None}]
    out = [m["id"] for m in order_candidates_for_narrative(cands)]
    assert out == ["early", "mid", "late", "undated"]


def test_narrative_order_ignores_a_lone_anchor() -> None:
    """One dated memory among many is not chronology.

    Live tributes carry a time anchor on ~1 moment in 33; hoisting that one to
    page one would be worse than leaving the telling order intact.
    """
    from flashback.tribute_video.context import order_candidates_for_narrative

    cands = [{"id": "c", "time_anchor": None},
             {"id": "b", "time_anchor": {"year": 1998}},
             {"id": "a", "time_anchor": None}]
    assert [m["id"] for m in order_candidates_for_narrative(cands)] == \
        ["a", "b", "c"]


def test_narrative_order_tolerates_junk_anchors() -> None:
    from flashback.tribute_video.context import order_candidates_for_narrative

    cands = [{"id": "b", "time_anchor": {"life_period": "First year of BTech"}},
             {"id": "a", "time_anchor": "sometime in the 90s"}]
    assert [m["id"] for m in order_candidates_for_narrative(cands)] == ["a", "b"]


def _pool(n, prefix="t"):
    return [{"id": f"{prefix}{i}", "time_anchor": None} for i in range(n)]


def test_thin_theme_pool_widens_to_the_person_pool() -> None:
    """The ready gate counts person-wide; the book must not be built from less.

    Live case: a subject with 9 qualifying memories had 2 tagged to the tribute
    theme, passed the gate, and got a two-memory video.
    """
    from flashback.tribute_video.context import choose_candidate_pool

    chosen = choose_candidate_pool(_pool(2, "themed"), _pool(9, "all"), target=3)
    assert len(chosen) == 9


def test_healthy_theme_pool_stays_on_theme() -> None:
    from flashback.tribute_video.context import choose_candidate_pool

    chosen = choose_candidate_pool(_pool(5, "themed"), _pool(9, "all"), target=3)
    assert [m["id"] for m in chosen] == [f"themed{i}" for i in range(5)]


def test_widening_never_shrinks_the_pool() -> None:
    """A theme pool of 2 beats a person pool of 1 -- widening must not lose material."""
    from flashback.tribute_video.context import choose_candidate_pool

    assert len(choose_candidate_pool(_pool(2, "themed"), _pool(1, "all"), target=3)) == 2
    assert choose_candidate_pool([], [], target=3) == []
