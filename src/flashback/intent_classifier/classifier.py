"""Intent Classifier implementation."""

from __future__ import annotations

from typing import Any

from flashback.intent_classifier.prompts import INTENT_TOOL, SYSTEM_PROMPT
from flashback.intent_classifier.schema import IntentResult
from flashback.llm.interface import Provider, call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.working_memory import Turn


class IntentClassifier:
    """Classify the most recent user turn using the shared LLM layer."""

    def __init__(
        self,
        settings,
        provider: Provider,
        model: str,
        timeout: float,
        max_tokens: int,
        transcript_window: int = 6,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._transcript_window = transcript_window

    async def classify(
        self,
        recent_turns: list[Turn],
        signals: dict[str, Any],
    ) -> IntentResult:
        """Classify the most recent user turn. Raises on LLM or validation failure."""
        windowed = recent_turns[-self._transcript_window :]
        user_block = self._build_user_block(windowed, signals)

        args = await call_with_tool(
            provider=self._provider,
            model=self._model,
            system_prompt=SYSTEM_PROMPT,
            user_message=user_block,
            tool=INTENT_TOOL,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            settings=self._settings,
        )
        return IntentResult.model_validate(args)

    def _build_user_block(self, turns: list[Turn], signals: dict[str, Any]) -> str:
        """Render transcript and signal context into a compact prompt block."""
        lines: list[str] = ["<turns>"]
        for turn in turns:
            lines.append(f"{turn.role}: {xml_text(turn.content)}")
        lines.extend(["</turns>", "<signals>"])
        for key in sorted(signals):
            if not key.startswith("signal_"):
                continue
            display_key = key.removeprefix("signal_")
            lines.append(f"{display_key}: {xml_text(signals[key])}")
        lines.append("</signals>")
        return "\n".join(lines)
