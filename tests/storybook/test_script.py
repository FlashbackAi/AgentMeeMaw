"""Script assembly — BookScript round-trip + the validated narrative prompt."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flashback.storybook.collections import COLLECTIONS
from flashback.storybook.script import BookScript, assemble_script

_SETTINGS = SimpleNamespace(
    llm_big_provider="anthropic", llm_big_model="claude-sonnet-4-6"
)


def _raw(pages: int = 7, panels: int = 3) -> dict:
    return {
        "cover_title": "T",
        "pages": [
            {
                "panels": [
                    {
                        "scene": "s",
                        "text": "t",
                        "kind": "caption",
                        "age_stage": "mid",
                    }
                ]
                * panels
            }
        ]
        * pages,
    }


def test_round_trip() -> None:
    s = BookScript.from_dict(_raw())
    assert len(s.pages) == 7
    assert len(s.pages[0].panels) == 3
    assert s.pages[0].panels[0].age_stage == "mid"
    assert BookScript.from_dict(s.to_dict()).to_dict() == s.to_dict()


def test_bad_age_stage_rejected() -> None:
    d = _raw()
    d["pages"][0]["panels"][0]["age_stage"] = "toddler"
    with pytest.raises(ValueError):
        BookScript.from_dict(d)


def test_bad_kind_rejected() -> None:
    d = _raw()
    d["pages"][0]["panels"][0]["kind"] = "banner"
    with pytest.raises(ValueError):
        BookScript.from_dict(d)


async def _assemble(collection, canned=None):
    canned = canned or _raw(panels=3 if collection.layout == "grid" else 1)
    with patch(
        "flashback.storybook.script.call_with_tool",
        new=AsyncMock(return_value=canned),
    ) as llm:
        out = await assemble_script(
            settings=_SETTINGS,
            collection=collection,
            subject_name="Subject",
            relationship="Grand Father",
            gt_context="<gt/>",
            moments=[{"title": "t", "narrative": "n"}],
        )
    return out, llm


async def test_assemble_returns_seven_page_script() -> None:
    out, llm = await _assemble(COLLECTIONS["childhood"])
    assert isinstance(out, BookScript)
    assert len(out.pages) == 7
    sys_prompt = llm.call_args.kwargs["system_prompt"]
    # The spike-validated arc + signature + quote + throughline rules.
    assert "CAUSE AND EFFECT" in sys_prompt
    assert "SIGNATURE IMAGE" in sys_prompt
    assert "final IMAGE, not a moral" in sys_prompt
    assert "THROUGHLINE" in sys_prompt
    assert "age_stage" in sys_prompt


async def test_gentle_tone_injects_child_safety_rules() -> None:
    _, llm = await _assemble(COLLECTIONS["childhood"])
    assert "NEVER show a child drinking toddy" in llm.call_args.kwargs[
        "system_prompt"
    ]


async def test_full_tone_omits_child_safety_rules() -> None:
    _, llm = await _assemble(
        COLLECTIONS["interesting"],
        canned=_raw(panels=3),
    )
    assert "NEVER show a child drinking toddy" not in llm.call_args.kwargs[
        "system_prompt"
    ]


async def test_edit_instructions_reach_the_prompt() -> None:
    with patch(
        "flashback.storybook.script.call_with_tool",
        new=AsyncMock(return_value=_raw()),
    ) as llm:
        await assemble_script(
            settings=_SETTINGS,
            collection=COLLECTIONS["childhood"],
            subject_name="S",
            relationship=None,
            gt_context="",
            moments=[{"title": "t", "narrative": "n"}],
            edit_instructions=["warmer", "more about the pond"],
        )
    user = llm.call_args.kwargs["user_message"]
    assert "family_edit_requests" in user
    assert "more about the pond" in user
