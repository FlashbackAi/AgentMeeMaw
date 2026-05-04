# Step 9 - Turn Orchestrator Formalization

This step refactors the turn orchestrator into a small state machine with
named steps, centralized failure policy, and metrics-ready structured logs.
The turn behavior from steps 5-8 is preserved; the only new runtime surface is
per-step and per-handler instrumentation.

## File Layout

```
src/flashback/orchestrator/
    deps.py                  OrchestratorDeps startup bundle
    errors.py                Orchestrator-domain HTTP-mapped errors
    failure_policy.py        DEGRADE vs PROPAGATE registry
    instrumentation.py       timed_step() helper
    orchestrator.py          thin handler shell / state machine
    protocol.py              unchanged HTTP-facing protocol
    state.py                 TurnState and SessionStartState
    steps/
        append_turn.py       user turn append
        classify.py          Intent Classifier + WM signal update
        retrieve.py          Retrieval Service fan-out
        select_question.py   Phase Gate switch selection
        generate_response.py Response Generator context/wiring
        append_response.py   assistant append + question tracking
        starter_opener.py    session-start person/opener/WM steps
```

`flashback.http.app` now constructs `OrchestratorDeps` once during lifespan
startup and passes it into `Orchestrator`. The real orchestrator owns Working
Memory writes. The HTTP routes retain a narrow compatibility branch for older
test doubles that do not advertise `owns_working_memory`.

## Failure Policy

Turn steps:

| Step | Policy | Rationale |
|---|---|---|
| `append_user_turn` | `PROPAGATE` | Working Memory append is critical. |
| `intent_classify` | `DEGRADE` | Continue with the existing `story` fallback. |
| `retrieve` | `DEGRADE` | Continue with empty graph context. |
| `select_question` | `DEGRADE` | Switch turns can generate without a seeded question. |
| `generate_response` | `PROPAGATE` | No acceptable canned fallback for the memorial reply. |
| `append_assistant` | `PROPAGATE` | Working Memory append is critical. |

Session-start steps:

| Step | Policy | Rationale |
|---|---|---|
| `load_person` | `PROPAGATE` | Missing person maps to 404. |
| `select_starter_anchor` | `PROPAGATE` | Missing starter seed is a deployment/config failure. |
| `generate_opener` | `PROPAGATE` | Opener generation failure maps to 503. |
| `init_working_memory` | `PROPAGATE` | Session cannot proceed without WM. |
| `append_opener` | `PROPAGATE` | Opener must be recorded for attribution. |

## Debugging Logs

Every turn binds `turn_id`, `session_id`, `person_id`, and `role_id`.
Every session start binds `session_id`, `person_id`, and `role_id`.

Useful log queries:

- Find a full turn: `turn_id=<uuid>`.
- Find slow steps: `event=step_complete duration_ms>...`.
- Find degraded turns: `event=step_degraded` or
  `event=turn_complete degraded_steps!=[]`.
- Find opener latency: `event=session_start_complete` plus the preceding
  `step_complete` records for the same `session_id`.

## Verified

- [x] Refactor keeps the existing HTTP/orchestrator behavior green.
- [x] New policy/state/instrumentation/logging tests added.
- [x] Full local suite with `.env.local` loaded:
      `python -m pytest -q` -> **213 passed**.
- [x] `python -m pytest --collect-only -q` reports **213 tests**.
- [x] `uvicorn flashback.http.app:create_app --factory` factory smoke passed
      with `--lifespan off` and dummy env vars.
- [x] `pyproject.toml` unchanged: no new top-level dependencies.
- [x] `.env.example` unchanged: no new env vars.

## Deviations

- The repo uses the established `src/flashback/...` package layout rather
  than the prompt shorthand `src/...`.
- `Orchestrator.__init__` accepts the new `OrchestratorDeps` object and still
  supports the older keyword-constructor path used by step 5-8 tests. The
  production startup path uses `OrchestratorDeps`.
- Session start preserves the existing no-orphan behavior: it loads the person
  and generates the opener before initializing Working Memory, so 404/503
  failures do not leave partial Valkey state.
- Retrieval step failures are converted into the known degradable LLM error
  family before entering the failure policy. This preserves the step 6 behavior
  that retrieval outages return a normal response with empty context.
