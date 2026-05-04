"""Provider-neutral tool definitions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolSpec(BaseModel):
    """Provider-neutral tool definition translated by ``interface.py``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any]
