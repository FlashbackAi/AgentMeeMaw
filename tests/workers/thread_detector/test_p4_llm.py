"""P4 LLM wrapper tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flashback.workers.thread_detector import p4_llm as p4_mod
from flashback.workers.thread_detector.p4_llm import (
    propose_thread_deepen_questions,
)
from flashback.workers.thread_detector.schema import (
    ClusterableMoment,
    ThreadSnapshot,
)


def _moments() -> list[ClusterableMoment]:
    return [
        ClusterableMoment(
            id=f"m{i}", title=f"t{i}", narrative=f"n{i}", embedding=[0.0] * 4
        )
        for i in range(3)
    ]


def _stub(returns):
    async def _impl(**kwargs):
        return returns

    return _impl


def test_one_question_with_themes(monkeypatch, stub_p4_cfg, stub_settings):
    monkeypatch.setattr(
        p4_mod,
        "call_with_tool",
        _stub(
            {
                "questions": [
                    {
                        "text": "What did the cabin look like inside?",
                        "themes": ["place", "summers"],
                    }
                ],
                "reasoning": "Surface a sensory detail.",
            }
        ),
    )

    result = propose_thread_deepen_questions(
        cfg=stub_p4_cfg,
        settings=stub_settings,
        person_name="Dad",
        thread=ThreadSnapshot(id="t1", name="Cabin summers", description="x"),
        member_moments=_moments(),
    )

    assert len(result.questions) == 1
    assert result.questions[0].themes == ["place", "summers"]


def test_two_questions(monkeypatch, stub_p4_cfg, stub_settings):
    monkeypatch.setattr(
        p4_mod,
        "call_with_tool",
        _stub(
            {
                "questions": [
                    {"text": "Q1?", "themes": ["a"]},
                    {"text": "Q2?", "themes": ["b", "c"]},
                ],
                "reasoning": "ok",
            }
        ),
    )

    result = propose_thread_deepen_questions(
        cfg=stub_p4_cfg,
        settings=stub_settings,
        person_name="Dad",
        thread=ThreadSnapshot(id="t", name="x", description="x"),
        member_moments=_moments(),
    )

    assert len(result.questions) == 2


def test_question_without_themes_rejected(monkeypatch, stub_p4_cfg, stub_settings):
    """Per CLAUDE.md §4 invariant #9, themes must be present."""
    monkeypatch.setattr(
        p4_mod,
        "call_with_tool",
        _stub(
            {
                "questions": [{"text": "Q?", "themes": []}],
                "reasoning": "x",
            }
        ),
    )

    with pytest.raises(ValidationError):
        propose_thread_deepen_questions(
            cfg=stub_p4_cfg,
            settings=stub_settings,
            person_name="Dad",
            thread=ThreadSnapshot(id="t", name="x", description="x"),
            member_moments=_moments(),
        )


def test_more_than_two_questions_rejected(
    monkeypatch, stub_p4_cfg, stub_settings
):
    monkeypatch.setattr(
        p4_mod,
        "call_with_tool",
        _stub(
            {
                "questions": [
                    {"text": "Q1?", "themes": ["a"]},
                    {"text": "Q2?", "themes": ["b"]},
                    {"text": "Q3?", "themes": ["c"]},
                ],
                "reasoning": "x",
            }
        ),
    )

    with pytest.raises(ValidationError):
        propose_thread_deepen_questions(
            cfg=stub_p4_cfg,
            settings=stub_settings,
            person_name="Dad",
            thread=ThreadSnapshot(id="t", name="x", description="x"),
            member_moments=_moments(),
        )
