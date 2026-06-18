"""assemble_tribute_script falls back to chronological when settings=None."""

from __future__ import annotations

from flashback.tribute.assembly import TributeScript, assemble_tribute_script


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
