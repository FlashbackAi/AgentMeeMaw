"""Naming LLM wrapper tests."""

from __future__ import annotations

import pytest

from flashback.llm.errors import LLMTimeout
from flashback.workers.thread_detector import naming_llm as naming_mod
from flashback.workers.thread_detector.naming_llm import name_cluster
from flashback.workers.thread_detector.schema import ClusterableMoment


def _moments() -> list[ClusterableMoment]:
    return [
        ClusterableMoment(
            id=f"m{i}", title=f"t{i}", narrative=f"n{i}", embedding=[0.0] * 4
        )
        for i in range(3)
    ]


def _stub(returns=None, exc=None):
    async def _impl(**kwargs):
        if exc is not None:
            raise exc
        return returns

    return _impl


def test_coherent_returns_populated_result(monkeypatch, stub_naming_cfg, stub_settings):
    monkeypatch.setattr(
        naming_mod,
        "call_with_tool",
        _stub(
            returns={
                "coherent": True,
                "reasoning": "common arc",
                "name": "Cabin summers",
                "description": "Summers at the lake cabin.",
                "generation_prompt": "A wooden cabin under warm sun.",
            }
        ),
    )

    result = name_cluster(
        cfg=stub_naming_cfg,
        settings=stub_settings,
        person_name="Dad",
        member_moments=_moments(),
    )

    assert result.coherent is True
    assert result.name == "Cabin summers"
    assert result.description.startswith("Summers")
    assert result.generation_prompt.startswith("A wooden cabin")


def test_incoherent_returns_coherent_false(
    monkeypatch, stub_naming_cfg, stub_settings
):
    monkeypatch.setattr(
        naming_mod,
        "call_with_tool",
        _stub(returns={"coherent": False, "reasoning": "noisy cluster"}),
    )

    result = name_cluster(
        cfg=stub_naming_cfg,
        settings=stub_settings,
        person_name="Dad",
        member_moments=_moments(),
    )

    assert result.coherent is False
    assert result.name is None
    assert result.description is None


def test_contributor_display_name_in_user_message(
    monkeypatch, stub_naming_cfg, stub_settings
):
    captured: dict = {}

    async def _impl(**kwargs):
        captured.update(kwargs)
        return {"coherent": False, "reasoning": "noisy"}

    monkeypatch.setattr(naming_mod, "call_with_tool", _impl)
    name_cluster(
        cfg=stub_naming_cfg,
        settings=stub_settings,
        person_name="Dad",
        member_moments=_moments(),
        contributor_display_name="Sarah",
    )
    assert (
        "<contributor_display_name>Sarah</contributor_display_name>"
        in captured["user_message"]
    )


def test_contributor_display_name_empty_renders_empty_tag(
    monkeypatch, stub_naming_cfg, stub_settings
):
    captured: dict = {}

    async def _impl(**kwargs):
        captured.update(kwargs)
        return {"coherent": False, "reasoning": "noisy"}

    monkeypatch.setattr(naming_mod, "call_with_tool", _impl)
    name_cluster(
        cfg=stub_naming_cfg,
        settings=stub_settings,
        person_name="Dad",
        member_moments=_moments(),
    )
    assert (
        "<contributor_display_name></contributor_display_name>"
        in captured["user_message"]
    )


def test_llm_timeout_propagates(monkeypatch, stub_naming_cfg, stub_settings):
    monkeypatch.setattr(
        naming_mod, "call_with_tool", _stub(exc=LLMTimeout("slow"))
    )

    with pytest.raises(LLMTimeout):
        name_cluster(
            cfg=stub_naming_cfg,
            settings=stub_settings,
            person_name="Dad",
            member_moments=_moments(),
        )
