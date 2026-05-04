"""Typed Intent Classifier output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Intent = Literal["clarify", "recall", "deepen", "story", "switch"]
Confidence = Literal["low", "medium", "high"]
Temperature = Literal["low", "medium", "high"]


class IntentResult(BaseModel):
    """Classifier result. Reasoning is for logs only."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: Confidence
    emotional_temperature: Temperature
    reasoning: str
