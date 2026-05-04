"""Small logging helpers for orchestrator timing."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def timed_step(logger, step_name: str, **bindings) -> Iterator[None]:
    """Log a ``step_complete`` event with elapsed milliseconds."""

    started = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000)
        logger.info(
            "step_complete",
            step=step_name,
            duration_ms=max(1, duration_ms),
            **bindings,
        )
