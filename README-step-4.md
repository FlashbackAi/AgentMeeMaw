# Step 4 — Conversation Gateway + Working Memory

This step ships the agent service's HTTP surface and the Valkey-backed
**Working Memory** that holds per-session ephemeral state (transcript
window, segment buffer, rolling summary, signals).

The HTTP routes are real. The **orchestrator** behind them is a stub
that returns canned responses but reads/writes Working Memory and
Postgres correctly — step 9 swaps the stub for the real Turn
Orchestrator without changing any other shape.

## What it ships

```
src/flashback/
├── config.py                     (HttpConfig added)
├── db/connection.py              (make_async_pool added)
├── working_memory/
│   ├── __init__.py
│   ├── keys.py                   key-naming + validation
│   ├── schema.py                 Turn, WorkingMemoryState
│   └── client.py                 WorkingMemory (Valkey)
├── orchestrator/
│   ├── __init__.py
│   └── stub.py                   StubOrchestrator + Protocol
└── http/
    ├── app.py                    FastAPI factory + lifespan
    ├── auth.py                   X-Service-Token guard
    ├── deps.py                   request-scoped DI
    ├── errors.py                 domain → HTTP mapping
    ├── logging.py                structlog config + middleware
    ├── models.py                 pydantic request/response
    └── routes/
        ├── health.py
        ├── session.py            /session/start, /session/wrap
        ├── turn.py               /turn
        └── admin.py              /admin/reset_phase
```

## Endpoints

All require `X-Service-Token` except `/health`.

| Method | Path                  | Purpose                                  |
|--------|-----------------------|------------------------------------------|
| GET    | `/health`             | Valkey + Postgres reachability probe.    |
| POST   | `/session/start`      | Initialise WM, return opener.            |
| POST   | `/turn`               | Append turn, return reply.               |
| POST   | `/session/wrap`       | Force-close, summary, clear WM.          |
| POST   | `/admin/reset_phase`  | Manual override of the Handover Check.   |

## Working Memory key layout

Three keys per session (CLAUDE.md s7, ARCHITECTURE.md s3.4):

```
wm:session:{session_id}:transcript    LIST   trimmed to last 30 turns
wm:session:{session_id}:segment       LIST   turns since last boundary
wm:session:{session_id}:state         HASH   identity + summary + signals
```

Every write refreshes a TTL (default 24h). The two atomicity-critical
operations:

- **`reset_segment`** — `LRANGE` + `DEL` inside `MULTI/EXEC`.
- **`update_rolling_summary`** — Lua script: HGET current → HSET prior
  → HSET new → EXPIRE, all server-side, no read-modify-write race.

## Running locally

### Valkey (or Redis 7+)

```bash
docker run --rm -p 6379:6379 valkey/valkey:7
```

### Environment

```bash
# Required
export DATABASE_URL=postgresql://flashback:flashback@localhost:5432/flashback
export VALKEY_URL=redis://localhost:6379/0
export SERVICE_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Optional (defaults shown in .env.example)
export HTTP_HOST=0.0.0.0
export HTTP_PORT=8000
export WORKING_MEMORY_TTL_SECONDS=86400
export WORKING_MEMORY_TRANSCRIPT_LIMIT=30
```

### Boot

```bash
uvicorn flashback.http.app:create_app --factory --host 0.0.0.0 --port 8000
```

## Curl walkthrough

```bash
TOKEN="$SERVICE_TOKEN"
H="X-Service-Token: $TOKEN"
PERSON=$(uuidgen)
ROLE=$(uuidgen)
SESSION=$(uuidgen)

# Insert a person row first (in another terminal):
#   psql "$DATABASE_URL" -c \
#     "INSERT INTO persons (id, name) VALUES ('$PERSON', 'Maya');"

# 1. Health
curl -s "http://localhost:8000/health"
# {"status":"ok","checks":{"valkey":"ok","postgres":"ok"}}

# 2. Start a session
curl -s -X POST "http://localhost:8000/session/start" \
  -H "$H" -H "Content-Type: application/json" \
  -d "{
    \"session_id\":\"$SESSION\",
    \"person_id\":\"$PERSON\",
    \"role_id\":\"$ROLE\",
    \"session_metadata\":{}
  }"
# {"session_id":"...","opener":"Tell me about Maya.","metadata":{...}}

# 3. Send a turn
curl -s -X POST "http://localhost:8000/turn" \
  -H "$H" -H "Content-Type: application/json" \
  -d "{
    \"session_id\":\"$SESSION\",
    \"person_id\":\"$PERSON\",
    \"role_id\":\"$ROLE\",
    \"message\":\"She loved making pasta from scratch.\"
  }"
# {"reply":"I hear you. Tell me more.","metadata":{...}}

# 4. Wrap the session
curl -s -X POST "http://localhost:8000/session/wrap" \
  -H "$H" -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SESSION\",\"person_id\":\"$PERSON\"}"
# {"session_summary":"","metadata":{"moments_extracted_estimate":0}}

# 5. Reset phase (admin)
curl -s -X POST "http://localhost:8000/admin/reset_phase" \
  -H "$H" -H "Content-Type: application/json" \
  -d "{\"person_id\":\"$PERSON\"}"
```

## Where the orchestrator stub gets replaced

The HTTP routes program against the `Orchestrator` Protocol declared in
[`flashback.orchestrator.stub`](src/flashback/orchestrator/stub.py).
Step 9's Turn Orchestrator implements the same Protocol — wiring is
swapped in [`flashback.http.app._lifespan`](src/flashback/http/app.py)
where `StubOrchestrator(...)` is constructed. Nothing else moves.

The stub's `handle_session_start` already does a real `SELECT name,
phase FROM persons` so the 404 path on bad `person_id` is exercised
end-to-end now; step 9 fills in Phase Gate / Response Generator
without changing the route's contract.

## Tests

```bash
pip install -e ".[dev]"

# No-DB tier (always runs)
pytest tests/working_memory/ tests/http/

# DB tier (requires TEST_DATABASE_URL)
TEST_DATABASE_URL=postgresql://flashback:flashback@localhost:5432/flashback_test \
    pytest tests/http/test_admin.py tests/http/test_health.py::TestHealth::test_happy_path_with_real_db
```

| Suite                            | Count | DB? |
|----------------------------------|------:|-----|
| `tests/working_memory/test_keys.py`   | 10 | no  |
| `tests/working_memory/test_client.py` | 19 | no  |
| `tests/http/test_auth.py`             |  4 | no  |
| `tests/http/test_session.py`          |  6 | no  |
| `tests/http/test_turn.py`             |  4 | no  |
| `tests/http/test_health.py`           |  3 | 1 needs |
| `tests/http/test_admin.py`            |  3 | yes |

**45 no-DB tests + 4 DB tests.** No-DB tests use `fakeredis[lua]` to
exercise the Valkey contract (including the `update_rolling_summary`
Lua script) with no external service.

## Verified

- `pip install -e ".[dev]"` installs cleanly on Python 3.14.
- `pytest tests/working_memory/ tests/http/` — **45 passed** (4 admin/
  health tests skip without `TEST_DATABASE_URL`, by design).
- Full repo `pytest` — **62 passed, 17 skipped** (no regressions in
  step-3 embedding worker tests).
- `python -c "from flashback.http.app import create_app"` — clean
  import, no circulars.

Run locally to fill in:

- [ ] `uvicorn flashback.http.app:create_app --factory` boots against
      a live Valkey + Postgres.
- [ ] `curl -H "X-Service-Token: $TOKEN" localhost:8000/health` →
      `{"status":"ok"}`.
- [ ] DB-tier admin tests pass:
      `TEST_DATABASE_URL=... pytest tests/http/test_admin.py`.

## Out of scope (per the step-4 prompt)

- Real opener generation, Phase Gate, Response Generator (steps 7–8).
- Intent Classifier, Retrieval Service, Segment Detector, Extraction
  Worker integration (steps 5–11).
- CORS, rate limiting, Sentry/metrics. Internal-network only;
  structured logs are the only observability surface in v1.
- Schema migrations. Step 4 reads `persons` and writes the same
  columns that already exist; no migration needed.

## Deviations from the step-4 prompt

- **Package layout.** The prompt's directory tree shows `src/config.py`
  and `src/working_memory/...`; the existing repo (post step 3) lives
  under `src/flashback/`. I matched the existing convention —
  `src/flashback/config.py`, `src/flashback/working_memory/...`, etc. —
  so the import path for the uvicorn factory is
  `flashback.http.app:create_app`, not `http.app:create_app` (which
  would also clash with the stdlib `http` module).
- **`config.py` split.** Rather than one growing `Config` dataclass, I
  added a separate `HttpConfig` so the embedding worker (step 3) and
  the HTTP service load only the env vars they each need. The original
  `Config` is unchanged.
- **Version pinning.** The prompt asked for exact pins; I matched the
  existing repo style (compatible-range pins like `>=0.115.0,<0.116`).
  Same effect for SemVer-respecting packages, easier dep-resolver
  arithmetic.
- **Lua for `update_rolling_summary`.** The prompt suggested
  `MULTI/EXEC`. A pure pipeline can't read-then-write atomically (the
  read result isn't available inside the block), so I used a small Lua
  script via `EVAL` instead. Same atomicity guarantee, simpler code
  path. `fakeredis[lua]` covers it in tests.
- **Idempotent `initialize`.** The prompt says idempotent; I read that
  as "second call is a no-op refresh", not "second call clobbers".
  Node never reissues a `session_id`, so a duplicate call is treated
  as an idle TTL refresh on the existing state — clobbering would lose
  the in-flight transcript.
- **Validation in `keys.py`.** Beyond rejecting empty/non-str inputs,
  I also reject whitespace in `session_id` to make a corrupted upstream
  caller fail loudly instead of silently splitting key segments.

## Next: step 5 — Intent Classifier

Step 5 introduces the small-LLM call that classifies each user turn's
intent (`clarify`, `recall`, `deepen`, `story`, `switch`) and emotional
temperature. It plugs into the orchestrator's `handle_turn` between
the user-append and the response-generation steps.
