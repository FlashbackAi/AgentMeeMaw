"""Shared shape for the ``latest_generation_context`` JSONB column.

This is the canonical artifact-generation context the agent writes to
Postgres on every auto / regenerate / edit push. Node's worker reads
this column at SQS-processing time — the SQS message itself is a
trigger only and does not carry prompt content. See migration 0023.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_generation_context(
    *,
    prompt: str,
    negative_prompt: str | None,
    mode: str,
    reference_s3_key: str | None,
    preset: str | None,
    source: str,
) -> dict[str, Any]:
    """Return the JSONB-ready context dict.

    ``source`` ∈ {``"auto"``, ``"regenerate"``, ``"edit"``}. ``mode`` ∈
    {``"no_reference"``, ``"with_reference"``}. ``preset`` is a slug from
    :mod:`flashback.artifacts.presets` (or ``None`` for the default look).
    Callers are responsible for resolving / validating the preset slug
    before calling here — this helper does no validation, it just shapes
    the dict so every write site emits the same keys.

    ``composed_at`` is stamped here in UTC ISO-8601 so the worker can
    skip stale messages if needed (race protection when two edits land
    in quick succession).
    """
    return {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "mode": mode,
        "reference_s3_key": reference_s3_key,
        "preset": preset,
        "source": source,
        "composed_at": datetime.now(timezone.utc).isoformat(),
    }
