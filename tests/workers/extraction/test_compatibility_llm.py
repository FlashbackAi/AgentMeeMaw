"""Compatibility LLM wrapper tests."""

from __future__ import annotations

import pytest

from flashback.llm.errors import LLMError, LLMMalformedResponse
from flashback.workers.extraction import compatibility_llm as compat_mod
from flashback.workers.extraction.compatibility_llm import judge_compatibility
from flashback.workers.extraction.refinement import RefinementCandidate
from flashback.workers.extraction.schema import ExtractedMoment


def _stub_call(returns: dict, exception: Exception | None = None):
    async def _impl(**kwargs):
        if exception is not None:
            raise exception
        return returns

    return _impl


_NEW = ExtractedMoment(
    title="x",
    narrative="They had pancakes Sunday morning.",
    generation_prompt="kitchen",
)
_CAND = RefinementCandidate(
    id="00000000-0000-0000-0000-000000000001",
    title="Pancakes",
    narrative="Pancakes were a Sunday ritual.",
    distance=0.12,
)


@pytest.mark.parametrize(
    "verdict",
    ["refinement", "contradiction", "independent"],
)
def test_each_verdict(monkeypatch, verdict, stub_compat_cfg, stub_settings) -> None:
    monkeypatch.setattr(
        compat_mod,
        "call_with_tool",
        _stub_call({"verdict": verdict, "reasoning": "x"}),
    )
    response = judge_compatibility(
        cfg=stub_compat_cfg,
        settings=stub_settings,
        new_moment=_NEW,
        candidate=_CAND,
    )
    assert response.verdict == verdict
    assert response.reasoning == "x"


def test_unknown_verdict_raises(monkeypatch, stub_compat_cfg, stub_settings) -> None:
    monkeypatch.setattr(
        compat_mod,
        "call_with_tool",
        _stub_call({"verdict": "wat", "reasoning": ""}),
    )
    with pytest.raises(LLMMalformedResponse):
        judge_compatibility(
            cfg=stub_compat_cfg,
            settings=stub_settings,
            new_moment=_NEW,
            candidate=_CAND,
        )


def test_llm_error_propagates(monkeypatch, stub_compat_cfg, stub_settings) -> None:
    monkeypatch.setattr(
        compat_mod,
        "call_with_tool",
        _stub_call({}, exception=LLMError("boom")),
    )
    with pytest.raises(LLMError):
        judge_compatibility(
            cfg=stub_compat_cfg,
            settings=stub_settings,
            new_moment=_NEW,
            candidate=_CAND,
        )
