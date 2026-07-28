"""assemble_tribute_script falls back to chronological when settings=None."""

from __future__ import annotations

from flashback.tribute.assembly import (
    TributeScript,
    _scene_block,
    _when_label,
    assemble_tribute_script,
    chronological_sort,
)


def test_chronological_sort_orders_by_life_stage_then_year() -> None:
    # Deliberately scrambled, as if extracted out of order.
    candidates = [
        {"id": "old", "time_anchor": {"life_period": "old age"}},
        {"id": "child", "time_anchor": {"life_period": "childhood"}},
        {"id": "y1990", "time_anchor": {"year": 1990}},
        {"id": "y1985", "time_anchor": {"year": 1985}},
        {"id": "teen", "time_anchor": {"life_period": "adolescence"}},
    ]
    ordered = [c["id"] for c in chronological_sort(candidates)]
    # Life-staged beats sort by stage; year-only beats trail, sorted by year.
    assert ordered == ["child", "teen", "old", "y1985", "y1990"]


def test_chronological_sort_sinks_undated_to_tail_stably() -> None:
    candidates = [
        {"id": "u1"},
        {"id": "dated", "time_anchor": {"year": 1970}},
        {"id": "u2", "time_anchor": {}},
    ]
    ordered = [c["id"] for c in chronological_sort(candidates)]
    assert ordered == ["dated", "u1", "u2"]


def test_chronological_sort_reads_decade_when_year_absent() -> None:
    candidates = [
        {"id": "b", "time_anchor": {"decade": "1990s"}},
        {"id": "a", "time_anchor": {"decade": "1970s"}},
    ]
    assert [c["id"] for c in chronological_sort(candidates)] == ["a", "b"]


def test_when_label_prefers_most_specific_field() -> None:
    assert _when_label({"year": 1984, "decade": "1980s"}) == "1984"
    assert _when_label({"decade": "1980s", "life_period": "youth"}) == "1980s"
    assert _when_label({"life_period": "childhood"}) == "childhood"
    assert _when_label({}) == ""


def test_scene_block_carries_when_attribute() -> None:
    block = _scene_block(
        {"id": "m1", "narrative": "A morning", "time_anchor": {"year": 1984}}
    )
    assert 'when="1984"' in block
    assert "A morning" in block
    # No time anchor -> no when attribute.
    assert "when=" not in _scene_block({"id": "m2", "narrative": "n"})


def test_tribute_script_has_defining_phrase_and_hero_line_fields() -> None:
    s = TributeScript(
        scenes=[], opening_caption="", closing_caption="", message_text=""
    )
    assert s.defining_phrase == ""
    assert s.hero_line == ""


async def test_fallback_takes_first_n_with_title_captions() -> None:
    candidates = [
        {"id": "m1", "title": "The workshop", "narrative": "n1"},
        {"id": "m2", "title": "Sunday lunch", "narrative": "n2"},
        {"id": "m3", "title": "The drive", "narrative": "n3"},
        {"id": "m4", "title": "Extra", "narrative": "n4"},
    ]
    script = await assemble_tribute_script(
        settings=None,
        candidates=candidates,
        message_text="Thank you, Dad.",
        person_name="Dad",
        person_relationship="father",
        max_scenes=3,
    )
    assert [s.moment_id for s in script.scenes] == ["m1", "m2", "m3"]
    assert script.scenes[0].caption == "The workshop"
    assert script.message_text == "Thank you, Dad."


async def test_fallback_orders_by_life_chronology_not_extraction_order() -> None:
    # Fed newest-extracted-first (reverse life order); fallback must re-order.
    candidates = [
        {"id": "late", "title": "Retirement", "time_anchor": {"life_period": "old age"}},
        {"id": "early", "title": "First bike", "time_anchor": {"life_period": "childhood"}},
    ]
    script = await assemble_tribute_script(
        settings=None,
        candidates=candidates,
        message_text="",
        person_name="Dad",
        person_relationship="father",
        max_scenes=6,
    )
    assert [s.moment_id for s in script.scenes] == ["early", "late"]


async def test_empty_candidates_yield_empty_script() -> None:
    script = await assemble_tribute_script(
        settings=None,
        candidates=[],
        message_text="hi",
        person_name="Dad",
        person_relationship=None,
        max_scenes=6,
    )
    assert script.scenes == []
    assert script.message_text == "hi"


async def test_confession_voice_falls_back_without_settings() -> None:
    # settings=None -> fallback script, never raises, regardless of confession.
    script = await assemble_tribute_script(
        settings=None,
        candidates=[{"id": "m1", "title": "A morning", "narrative": "n1"}],
        message_text="",
        person_name="Dad",
        person_relationship="father",
        max_scenes=3,
        confession=True,
    )
    assert script.scenes  # fell back to chronological
    assert script.defining_phrase == ""  # fallback emits no cover lines
    assert script.hero_line == ""
