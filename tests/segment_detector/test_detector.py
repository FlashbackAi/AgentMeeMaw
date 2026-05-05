from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from flashback.llm.errors import LLMTimeout
from flashback.segment_detector import detector as detector_module
from flashback.segment_detector.detector import SegmentDetector
from flashback.segment_detector.prompts import SYSTEM_PROMPT_FORCE, SYSTEM_PROMPT_NORMAL
from tests.segment_detector.fixtures.sample_segments import SAMPLE_SEGMENT

SETTINGS = SimpleNamespace(openai_api_key="openai-key", anthropic_api_key="anthropic-key")


def _detector() -> SegmentDetector:
    return SegmentDetector(
        settings=SETTINGS,
        provider="openai",
        model="gpt-5.1",
        timeout=10,
        max_tokens=600,
    )


def _args(**overrides):
    data = {
        "boundary_detected": False,
        "reasoning": "The topic is still moving.",
    }
    data.update(overrides)
    return data


async def test_detect_returns_no_boundary(monkeypatch):
    monkeypatch.setattr(
        detector_module,
        "call_with_tool",
        AsyncMock(return_value=_args()),
    )

    result = await _detector().detect(SAMPLE_SEGMENT, "")

    assert result.boundary_detected is False
    assert result.rolling_summary is None


async def test_detect_returns_boundary_with_summary(monkeypatch):
    monkeypatch.setattr(
        detector_module,
        "call_with_tool",
        AsyncMock(
            return_value=_args(
                boundary_detected=True,
                rolling_summary="The contributor talked about Sunday pasta.",
            )
        ),
    )

    result = await _detector().detect(SAMPLE_SEGMENT, "Earlier context.")

    assert result.boundary_detected is True
    assert result.rolling_summary == "The contributor talked about Sunday pasta."


async def test_boundary_without_summary_raises_validation_error(monkeypatch):
    monkeypatch.setattr(
        detector_module,
        "call_with_tool",
        AsyncMock(return_value=_args(boundary_detected=True)),
    )

    with pytest.raises(ValidationError):
        await _detector().detect(SAMPLE_SEGMENT, "")


async def test_force_overrides_false_decision_and_logs(monkeypatch):
    monkeypatch.setattr(
        detector_module,
        "call_with_tool",
        AsyncMock(
            return_value=_args(
                boundary_detected=False,
                rolling_summary="Final session summary.",
            )
        ),
    )

    with capture_logs() as logs:
        result = await _detector().detect(SAMPLE_SEGMENT, "", force=True)

    assert result.boundary_detected is True
    assert result.rolling_summary == "Final session summary."
    assert any(
        record["event"] == "force_mode_overrode_decision"
        for record in logs
    )


async def test_normal_mode_preserves_false_decision(monkeypatch):
    monkeypatch.setattr(
        detector_module,
        "call_with_tool",
        AsyncMock(return_value=_args(boundary_detected=False)),
    )

    result = await _detector().detect(SAMPLE_SEGMENT, "", force=False)

    assert result.boundary_detected is False


async def test_llm_timeout_propagates(monkeypatch):
    monkeypatch.setattr(
        detector_module,
        "call_with_tool",
        AsyncMock(side_effect=LLMTimeout("slow")),
    )

    with pytest.raises(LLMTimeout):
        await _detector().detect(SAMPLE_SEGMENT, "")


async def test_force_flag_selects_force_prompt(monkeypatch):
    call = AsyncMock(return_value=_args(rolling_summary="Final summary."))
    monkeypatch.setattr(detector_module, "call_with_tool", call)

    await _detector().detect(SAMPLE_SEGMENT, "", force=True)

    assert call.await_args.kwargs["system_prompt"] == SYSTEM_PROMPT_FORCE


async def test_normal_flag_selects_normal_prompt(monkeypatch):
    call = AsyncMock(return_value=_args())
    monkeypatch.setattr(detector_module, "call_with_tool", call)

    await _detector().detect(SAMPLE_SEGMENT, "", force=False)

    assert call.await_args.kwargs["system_prompt"] == SYSTEM_PROMPT_NORMAL
