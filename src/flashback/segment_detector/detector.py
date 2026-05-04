"""Segment Detector implementation."""

from __future__ import annotations

import structlog

from flashback.llm.interface import Provider, call_with_tool
from flashback.segment_detector.prompts import (
    SEGMENT_DETECTOR_TOOL,
    SYSTEM_PROMPT_FORCE,
    SYSTEM_PROMPT_NORMAL,
)
from flashback.segment_detector.schema import SegmentDetectionResult
from flashback.working_memory import Turn

log = structlog.get_logger("flashback.segment_detector")


class SegmentDetector:
    """Detect segment boundaries and regenerate rolling summaries."""

    def __init__(
        self,
        settings,
        provider: Provider,
        model: str,
        timeout: float,
        max_tokens: int,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens

    async def detect(
        self,
        segment_turns: list[Turn],
        prior_rolling_summary: str,
        *,
        force: bool = False,
    ) -> SegmentDetectionResult:
        """
        Run the detector.

        ``force=True`` is the Session Wrap path: the LLM still writes
        the final summary, but the local result is forced to boundary.
        """

        system_prompt = SYSTEM_PROMPT_FORCE if force else SYSTEM_PROMPT_NORMAL
        args = await call_with_tool(
            provider=self._provider,
            model=self._model,
            system_prompt=system_prompt,
            user_message=self._build_user_message(
                segment_turns,
                prior_rolling_summary,
            ),
            tool=SEGMENT_DETECTOR_TOOL,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            settings=self._settings,
        )
        result = SegmentDetectionResult.model_validate(args)

        if force and not result.boundary_detected:
            log.warning(
                "force_mode_overrode_decision",
                reason=result.reasoning,
            )
            result = SegmentDetectionResult(
                boundary_detected=True,
                rolling_summary=result.rolling_summary,
                reasoning=result.reasoning,
            )

        return result

    def _build_user_message(
        self,
        segment_turns: list[Turn],
        prior_rolling_summary: str,
    ) -> str:
        """Render prior summary and segment turns into prompt context."""

        lines: list[str] = [
            "<prior_rolling_summary>",
            prior_rolling_summary or "",
            "</prior_rolling_summary>",
            "",
            "<current_segment>",
        ]
        for turn in segment_turns:
            lines.append(f"{turn.role}: {turn.content}")
        lines.append("</current_segment>")
        return "\n".join(lines)
