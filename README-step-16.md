# README-step-16 - Session Wrap

Step 16 wires the final end-to-end component: `POST /session/wrap`.
The endpoint now force-closes the open segment, generates the
next-session recap, fans out post-session work, and clears Working
Memory.

## What Changed

New HTTP-side producers:

- `flashback.queues.trait_synthesizer`
- `flashback.queues.profile_summary`
- `flashback.queues.producers_per_session`

New session-summary package:

- `flashback.session_summary.generator`
- `flashback.session_summary.prompts`
- `flashback.session_summary.schema`

New orchestrator pieces:

- `SessionWrapState`
- `SESSION_WRAP_POLICIES`
- `flashback.orchestrator.steps.wrap_session`
- real `Orchestrator.handle_session_wrap`

Working Memory now tracks `segments_pushed_this_session`. Natural
segment boundaries increment it in `detect_segment`; the wrap
force-close path increments it after the final extraction queue push.

## Wrap Sequence

1. Load Working Memory and the person row.
2. Read the open segment buffer.
3. If it is non-empty, call Segment Detector with `force=True`.
4. Push the force-closed segment to the extraction queue.
5. Update the rolling summary, reset the segment, clear the seeded
   question, and increment the segment counter.
6. Generate a short session summary from the final rolling summary.
7. Push trait synthesis, profile summary, and P2 producer jobs in
   parallel.
8. Return the summary and segment count.
9. Clear Working Memory best-effort.

## Lazy Ordering

The implementation intentionally uses lazy ordering. Session Wrap does
not wait for the Extraction Worker to drain before pushing the three
post-session queues. Each consumer is idempotent and degrade-soft, so
the practical tradeoff is one cycle of slightly stale synthesis in rare
races rather than distributed coordination in the hot wrap path.

## Summary Shape

The session summary is a fragment designed to slot into Node's next
opener:

```text
Last time, you talked about [fragment].
```

The prompt asks for output like `the summers at the lake cabin and your
grandfather's old red truck`, with no preamble, quotes, or final period.
It is separate from the rolling summary: the rolling summary is compact
agent memory; this fragment is a contributor-facing anchor.

## API Change

`/session/wrap` now returns:

```json
{
  "session_summary": "<string>",
  "metadata": {
    "segments_extracted_count": 1
  }
}
```

The old stub field `moments_extracted_estimate` was renamed to
`segments_extracted_count`. The value counts extraction queue pushes
from this session, including natural boundaries and the final
force-close.

## Fail-Soft Policy

Only missing Working Memory propagates as a 409. Force-close failure,
session-summary failure, queue-push failure, and WM clear failure are
recorded in `SessionWrapState.failures` and logged, but the handler
still returns 200 with whatever summary and segment count are available.

## Verified

- [x] `python -m pytest tests\session_summary tests\queues\test_trait_synthesizer_producer.py tests\queues\test_profile_summary_producer.py tests\queues\test_producers_per_session_producer.py tests\orchestrator\steps\test_wrap_session.py tests\orchestrator\test_wrap_state.py tests\orchestrator\test_orchestrator_session_wrap.py tests\working_memory\test_segments_counter.py tests\orchestrator\test_detect_segment_step.py tests\http\test_session.py -q` - 39 passed
- [x] `python -m pytest` - 508 passed
- [x] app factory boot check - `Flashback Agent`
