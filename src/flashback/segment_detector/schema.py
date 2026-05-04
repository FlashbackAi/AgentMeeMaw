"""Pydantic schema for Segment Detector tool output."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator


class SegmentDetectionResult(BaseModel):
    """Boundary decision plus conditional rolling-summary rewrite."""

    model_config = ConfigDict(extra="forbid")

    boundary_detected: bool
    rolling_summary: str | None = None
    reasoning: str

    @model_validator(mode="after")
    def summary_required_on_boundary(self) -> "SegmentDetectionResult":
        if self.boundary_detected and not self.rolling_summary:
            raise ValueError(
                "rolling_summary is required when boundary_detected=True"
            )
        return self
