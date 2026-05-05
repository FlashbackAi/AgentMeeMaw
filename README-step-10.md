# Step 10 - Segment Detector

This step adds the LLM-driven Segment Detector, the extraction queue
producer, and the final turn-loop step that closes coherent conversation
segments.

## What It Ships

```
src/flashback/
    segment_detector/
        detector.py       SegmentDetector.detect(force=False)
        prompts.py        normal + force prompts and tool schema
        schema.py         SegmentDetectionResult
    queues/
        client.py         async wrapper over sync boto3 send_message
        extraction.py     extraction queue payload producer
    orchestrator/
        steps/
            detect_segment.py
        failure_policy.py detect_segment = DEGRADE
        state.py          segment_boundary_detected flag
        orchestrator.py   final step after append_assistant
```

The package layout follows the existing `src/flashback/...` structure.

## User-Turn Cadence

`SEGMENT_DETECTOR_USER_TURN_CADENCE` controls how often the detector
gets called. The default is `6`. A "user turn" is one user message
plus the assistant reply.

The orchestrator increments
`signal_user_turns_since_segment_check` in Working Memory each time a
user message is appended. The detector is gated on that counter — when
it reaches the cadence value, the detector runs and the counter is
reset to `0`, regardless of whether a boundary fires. Between runs, the
detector is a no-op skip.

Buffer length is no longer the gate. The segment buffer still grows
across turns and is read at run time so the LLM sees the full window;
it is only cleared when a boundary fires.

## Single LLM Call

The detector uses one forced tool call:

```
boundary_detected: bool
rolling_summary: str | None
reasoning: str
```

`rolling_summary` is required only when `boundary_detected=true`. This
folds boundary detection and rolling-summary regeneration into a single
small-model round trip, keeping the summary owned by the Segment
Detector path without adding a second call on boundary turns.

## Boundary Order

On boundary, the orchestrator:

1. Reads segment turns from Working Memory.
2. Reads the prior rolling summary from Working Memory.
3. Calls the Segment Detector.
4. Pushes the extraction payload to SQS.
5. Updates the Working Memory rolling summary.
6. Resets the segment buffer.
7. Clears `last_seeded_question_id`.

The SQS push happens before Working Memory mutation. If the push fails,
the segment buffer and rolling summary stay untouched so the next turn
can evaluate the same segment again.

## SQS Failure Caveat

`detect_segment` is `DEGRADE`. Detector failures and extraction queue
failures are logged and the turn response still returns to Node. Because
SQS is pushed before Working Memory is mutated, a send failure leaves
the segment open for the next evaluation. A prolonged hard SQS outage
can still lose tail material if the session state expires before a
successful retry; this is a known v1 limitation until the step 11 worker
and infra-side DLQ story are exercised end to end.

## Synchronous Handler

The detector is awaited inside `/turn` for v1. This keeps the response
metadata truthful: `metadata.segment_boundary` is set from the actual
boundary result. Moving the call to a background task later should be a
small orchestration refactor, because the detector and queue producer
already sit behind a single step.

## Configuration

```
LLM_SEGMENT_DETECTOR_PROVIDER=openai
LLM_SEGMENT_DETECTOR_MODEL=gpt-5.1
LLM_SEGMENT_DETECTOR_TIMEOUT_SECONDS=10
LLM_SEGMENT_DETECTOR_MAX_TOKENS=600
SEGMENT_DETECTOR_USER_TURN_CADENCE=6
EXTRACTION_QUEUE_URL=...
```

The detector provider/model inherit the small-call defaults when unset.
The HTTP service constructs a sync boto3 SQS client and wraps
`send_message` with `asyncio.to_thread`, avoiding a new async AWS
dependency for v1.

## Verified

- [x] Prompt and schema tests cover JSON Schema validity, conditional
      summary validation, and force/normal prompt separation.
- [x] Detector tests cover boundary/no-boundary, force override,
      prompt selection, validation failure, and timeout propagation.
- [x] Queue tests cover JSON serialization, payload shape, null seeded
      question ids, and SQS exception propagation.
- [x] Orchestrator step tests cover threshold skip, no-boundary,
      boundary mutation order, SQS failure, LLM failure, and seeded
      question flow.
- [x] `pyproject.toml` already includes `boto3` from step 3.
- [x] Full local suite: `python -m pytest -q` -> 240 passed.
- [x] `python -m pytest --collect-only -q` reports 240 tests.
- [x] `uvicorn flashback.http.app:create_app --factory` boot smoke
      passed with dummy env vars and `--lifespan off`.

## Deviations

- Package paths use `src/flashback/...`, matching the repo established
  in step 3 rather than the prompt shorthand `src/...`.
- The async SQS wrapper lets the original boto3 exception propagate, so
  low-level queue tests can assert the exact exception. The orchestrator
  step wraps push failures in `QueueSendError` before they enter the
  failure policy.
- In older test-only orchestrator construction paths, the segment
  detector and extraction queue are optional. Production startup wires
  both dependencies.
