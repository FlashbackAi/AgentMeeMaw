from __future__ import annotations

import time

import structlog
from structlog.testing import capture_logs

from flashback.orchestrator.instrumentation import timed_step


def test_timed_step_logs_duration_and_bindings():
    logger = structlog.get_logger("test.instrumentation")

    with capture_logs() as logs:
        with timed_step(logger, "retrieve", n_moments=2):
            time.sleep(0.002)

    assert len(logs) == 1
    record = logs[0]
    assert record["event"] == "step_complete"
    assert record["step"] == "retrieve"
    assert record["duration_ms"] > 0
    assert record["n_moments"] == 2
