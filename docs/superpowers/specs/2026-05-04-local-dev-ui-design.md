# Local Dev UI — Design Spec
_2026-05-04_

## Goal

A local-only developer tool that lets you chat with the Flashback agent exactly as a real user would, while surfacing every LLM call, DB write, queue push, and Working Memory state change in real time in a browser panel alongside the chat.

**Hard constraint:** Nothing in `local/` is committed to git. No production code (`src/`) is modified. The instrumentation layer only activates when `FLASHBACK_DEV_MODE=true`.

---

## File Layout

```
local/                      # gitignored entirely
├── dev.py                  # Entry point — run this one command
├── server.py               # aiohttp dev server: WebSocket hub + /state endpoint
├── middleware.py           # Monkey-patches LLM/queue/DB clients → emits events
└── static/
    └── index.html          # Single-file chat UI + instrumentation panel
```

`.gitignore` gets one new line: `local/`

---

## How It Works

1. `python local/dev.py` — reads `.env.local`, sets `FLASHBACK_DEV_MODE=true`, spawns the flashback HTTP service (`uvicorn`) on port 8000, starts the aiohttp dev server on port 3001, opens browser at `http://localhost:3001`
2. On startup, `middleware.py` is imported by the flashback app (lifespan hook, gated by `FLASHBACK_DEV_MODE`). It monkey-patches:
   - Anthropic + OpenAI LLM client call methods
   - All SQS queue client `send_message` methods
   - The asyncpg/psycopg execute path for INSERT/UPDATE
3. Every patched call emits a JSON event into a global `asyncio.Queue` (the event bus)
4. `server.py` drains the event bus and broadcasts to all connected WebSocket clients
5. Browser receives events over WebSocket and renders them in the instrumentation panel in real time
6. The `/state` endpoint in `server.py` queries Valkey and Postgres directly (same `.env.local` credentials) and returns a JSON snapshot of Working Memory + DB row counts for the current session

---

## Event Schema

All events share a base shape:

```json
{
  "id": "uuid4",
  "ts": "ISO-8601",
  "type": "llm_call | queue_push | db_write | intent | segment | embedding",
  "payload": { ... }
}
```

### `llm_call`
```json
{
  "purpose": "intent_classifier | response_generator | segment_detector | session_summary",
  "model": "gpt-5.1 | claude-sonnet-4-6",
  "response_preview": "first 200 chars of response",
  "latency_ms": 342
}
```

### `queue_push`
```json
{
  "queue": "flashback-extraction | flashback-embedding | ...",
  "summary": "human-readable one-liner of what was pushed",
  "payload_keys": ["session_id", "person_id", "segments"]
}
```

### `db_write`
```json
{
  "table": "moments | entities | traits | edges | questions | persons",
  "operation": "INSERT | UPDATE",
  "row_count": 1,
  "preview": "id=uuid, content=first 60 chars..."
}
```

### `intent`
```json
{
  "intent": "recall | clarify | switch | reflect",
  "confidence": 0.92,
  "emotional_temperature": "low | medium | high"
}
```

### `segment`
```json
{
  "boundary": true,
  "turn_count": 7,
  "reasoning_preview": "first 100 chars of segment detector reasoning"
}
```

### `embedding`
```json
{
  "target_table": "moments | entities | traits",
  "target_id": "uuid",
  "content_preview": "first 60 chars",
  "model": "voyage-3"
}
```

---

## UI Layout

```
┌─────────────────────────────┬─────────────────────────────┐
│        CHAT PANEL           │    INSTRUMENTATION PANEL    │
│                             │                             │
│  [person name] — [phase]    │  ● llm_call  (blue)        │
│                             │  ● queue_push (orange)      │
│  Agent: Tell me about...    │  ● db_write  (green)        │
│                             │  ● segment   (purple)       │
│  You: He always cooked...   │  ● intent    (gray)         │
│                             │  ● embedding (teal)         │
│  Agent: That sounds...      │                             │
│                             │  [collapsible event cards]  │
│  ─────────────────────────  │                             │
│  [message input]  [Send]    │  [Working Memory snapshot]  │
│                             │  [DB row counts]            │
└─────────────────────────────┴─────────────────────────────┘
│  Intent: recall  Temp: medium  Phase: starter  Segment: –  │
└─────────────────────────────────────────────────────────────┘
```

- Left panel: chat bubbles, session start form (person_id, role_id UUIDs), Send button
- Right panel: live event stream, newest at top, each event a collapsible card color-coded by type
- Bottom bar: current session metadata updated after each turn
- Single HTML file, vanilla JS + CSS grid, no npm, no build step

---

## Session Flow in the UI

1. User enters `person_id` and `role_id` UUIDs → clicks **Start Session** → UI POSTs to `POST /session/start` on port 8000
2. Opener message appears in chat panel; events from session start appear in instrumentation panel
3. User types a message → clicks **Send** → UI POSTs to `POST /turn` → reply appears; events stream in real time
4. **End Session** button → POSTs to `POST /session/wrap` → session summary shown; background job events visible in panel
5. **Clear** resets the UI state (does not affect DB)

---

## Dependencies

All already available in the project's Python environment:
- `aiohttp` — dev server + WebSocket
- `asyncpg` or `psycopg` — direct DB reads for `/state`
- `redis` / `valkey` — direct Valkey reads for `/state`
- `boto3` — SQS queue inspection
- `uvicorn` — subprocess for the flashback HTTP service

No new packages needed beyond what's already in `pyproject.toml`.

---

## Isolation Guarantees

- `local/` is gitignored — nothing here is ever committed
- `middleware.py` only loads when `FLASHBACK_DEV_MODE=true` — the env var is never set in production
- No `src/` files are modified
- The dev server runs on a separate port (3001) — no conflict with the production service port (8000)
- The tool reads `.env.local` for credentials — same file that's already gitignored
